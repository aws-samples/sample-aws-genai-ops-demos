"""
Data Extractor for AWS Services Lifecycle Tracker
Combines HTML table parsing with LLM-guided extraction for high-quality data extraction
Handles the low-level extraction mechanics: HTML parsing, LLM processing, and data normalization
"""
import os
import requests
from bs4 import BeautifulSoup
import json
import re
import time
from datetime import datetime, timezone
import boto3
from botocore.exceptions import ClientError
from typing import Dict, List, Any, Optional

from aws_utils import get_region
from database_reads import get_service_config
from service_filters import apply_service_filters


class DataExtractor:
    """
    Hybrid data extractor that combines HTML parsing with LLM processing
    
    Process:
    1. Fetch HTML documentation pages
    2. Parse HTML tables using BeautifulSoup
    3. Use LLM to extract and normalize deprecation data
    4. Return structured data ready for storage
    """
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        
        # Initialize Bedrock client using deployment region
        bedrock_region = get_region()
        self.bedrock = boto3.client('bedrock-runtime', region_name=bedrock_region)
    
    def extract_service_data(self, service_name: str, force_refresh: bool = False, override_urls: List[str] = None) -> Dict[str, Any]:
        """
        Extract deprecation data for a specific AWS service
        
        Args:
            service_name: AWS service identifier (e.g., 'lambda', 'eks')
            force_refresh: Whether to force fresh extraction (ignored for now)
            override_urls: Optional URLs to override service config URLs
            
        Returns:
            dict: Extraction results with success status, items, and processing details
        """
        
        # Start timing the extraction
        start_time = time.time()
        
        # Get service configuration
        config_result = get_service_config(service_name)
        if 'error' in config_result:
            return {
                'success': False,
                'service_name': service_name,
                'error': config_result['error']
            }
        
        service_config = config_result
        service_config['service_name'] = service_name  # Add service_name for filtering
        
        print(f"\n=== Extracting {service_config['name']} ===")
        
        # Use override URLs if provided, otherwise use config URLs
        urls_to_process = override_urls or service_config.get('documentation_urls', [])
        
        if not urls_to_process:
            return {
                'success': False,
                'service_name': service_name,
                'error': 'No documentation URLs configured for this service'
            }
        
        all_extracted_items = []
        processing_results = []
        
        # Process each documentation URL
        for url in urls_to_process:
            try:
                # Step 1: Fetch and parse HTML tables
                raw_data = self._fetch_html_tables(url)
                
                if raw_data['status'] != 'success':
                    processing_results.append({
                        "url": url,
                        "success": False,
                        "error": raw_data.get('error', 'Failed to fetch HTML tables')
                    })
                    continue
                
                # Step 2: Use LLM to extract and normalize deprecation data
                normalized_data = self._llm_extract_deprecation_data(
                    raw_data['extracted_data'], 
                    service_config
                )
                
                if 'error' in normalized_data:
                    processing_results.append({
                        "url": url,
                        "success": False,
                        "error": normalized_data['error']
                    })
                    continue
                
                # Add source URL to each item
                items = normalized_data.get('items', [])
                for item in items:
                    item['source_url'] = url
                
                # Step 3: Apply service-specific filters
                filtered_items = apply_service_filters(service_name, items)
                
                all_extracted_items.extend(filtered_items)
                processing_results.append({
                    "url": url,
                    "success": True,
                    "items_extracted": len(filtered_items),
                    "items_before_filtering": len(items),
                    "tables_found": raw_data['tables_found']
                })
                
            except Exception as url_error:
                processing_results.append({
                    "url": url,
                    "success": False,
                    "error": str(url_error)
                })
        
        # Calculate extraction duration
        end_time = time.time()
        extraction_duration = round(end_time - start_time, 2)  # Round to 2 decimal places
        
        print(f"=== Extraction completed in {extraction_duration} seconds ===")
        
        return {
            'success': len(all_extracted_items) > 0,
            'service_name': service_name,
            'service_display_name': service_config['name'],
            'extraction_date': datetime.now(timezone.utc).isoformat(),
            'extraction_duration': extraction_duration,  # Add duration in seconds
            'total_items_extracted': len(all_extracted_items),
            'urls_processed': len(urls_to_process),
            'processing_results': processing_results,
            'items': all_extracted_items
        }
    
    def _fetch_html_tables(self, url: str) -> Dict[str, Any]:
        """
        Fetch HTML page and extract raw table data
        
        Args:
            url: Documentation URL to fetch
            
        Returns:
            dict: Status and extracted table data
        """
        
        print(f"Fetching: {url}")
        
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Find all table-contents divs (AWS docs structure)
            table_divs = soup.find_all('div', class_=['table-contents disable-scroll', 'table-contents'])
            
            print(f"Found {len(table_divs)} table-contents divs")
            
            extracted_data = []
            
            # Process table-contents divs
            for i, div in enumerate(table_divs):
                table_data = self._extract_table_data_from_div(div)
                if table_data:
                    extracted_data.append({
                        'type': 'table-contents-div',
                        'index': i + 1,
                        'data': table_data
                    })
            
            # If no table-contents divs found, try regular tables
            if not extracted_data:
                tables = soup.find_all('table')
                print(f"Found {len(tables)} regular tables")
                
                for i, table in enumerate(tables):
                    table_data = self._extract_table_data_from_table(table)
                    if table_data and table_data['rows']:
                        extracted_data.append({
                            'type': 'regular-table',
                            'index': i + 1,
                            'data': table_data
                        })
            
            return {
                'status': 'success',
                'url': url,
                'tables_found': len(extracted_data),
                'extracted_data': extracted_data
            }
            
        except Exception as e:
            return {
                'status': 'error',
                'url': url,
                'error': str(e),
                'extracted_data': []
            }
    
    def _extract_table_data_from_div(self, div) -> Optional[Dict[str, Any]]:
        """Extract structured data from a table-contents div"""
        table = div.find('table')
        if not table:
            return None
        return self._extract_table_data_from_table(table)
    
    def _extract_table_data_from_table(self, table) -> Dict[str, Any]:
        """
        Extract structured data from a table element
        
        Args:
            table: BeautifulSoup table element
            
        Returns:
            dict: Structured table data with headers and rows
        """
        
        # Extract headers
        headers = []
        header_row = table.find('tr')
        if header_row:
            for th in header_row.find_all(['th', 'td']):
                headers.append(th.get_text(strip=True))
        
        # Extract data rows
        rows = []
        for tr in table.find_all('tr')[1:]:  # Skip header row
            row_data = []
            for td in tr.find_all(['td', 'th']):
                cell_text = td.get_text(strip=True)
                row_data.append(cell_text)
            
            if row_data:  # Only add non-empty rows
                rows.append(row_data)
        
        # Convert to structured format
        structured_data = []
        for row in rows:
            if len(row) >= len(headers):
                row_dict = {}
                for i, header in enumerate(headers):
                    if i < len(row):
                        row_dict[header] = row[i]
                structured_data.append(row_dict)
        
        return {
            'headers': headers,
            'rows': structured_data,
            'raw_rows': rows
        }
    
    def _llm_extract_deprecation_data(self, raw_tables: List[Dict], service_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Use LLM to extract and normalize deprecation data from raw tables
        
        Args:
            raw_tables: List of raw table data from HTML parsing
            service_config: Service configuration with extraction instructions
            
        Returns:
            dict: Normalized deprecation data or error information
        """
        
        # Prepare the prompt
        prompt = self._build_extraction_prompt(raw_tables, service_config)
        
        # Call LLM
        try:
            response = self._call_bedrock_llm(prompt)
            
            # Parse JSON response (handle markdown code blocks)
            try:
                # Remove markdown code blocks if present
                clean_response = response.strip()
                print(f"Raw response length: {len(clean_response)}")
                print(f"Response starts with: {repr(clean_response[:50])}")
                
                if clean_response.startswith('```json'):
                    clean_response = clean_response[7:]  # Remove ```json
                if clean_response.endswith('```'):
                    clean_response = clean_response[:-3]  # Remove ```
                clean_response = clean_response.strip()
                
                print(f"Clean response length: {len(clean_response)}")
                print(f"Clean response starts with: {repr(clean_response[:50])}")
                
                normalized_data = json.loads(clean_response)
                print(f"Successfully parsed JSON with {len(normalized_data.get('items', []))} items")
                
                return normalized_data
            except json.JSONDecodeError as e:
                print(f"JSON parsing error: {str(e)}")
                print(f"Error at position {e.pos if hasattr(e, 'pos') else 'unknown'}")
                print(f"Failed to parse around position: {repr(clean_response[max(0, e.pos-100):min(len(clean_response), e.pos+100)] if hasattr(e, 'pos') else 'N/A')}")
                print(f"Full response (first 2000 chars): {response[:2000]}")
                return {
                    'error': f'Failed to parse LLM response as JSON: {str(e)}',
                    'raw_response': response[:2000],  # Increased to 2000 chars for better debugging
                    'parse_error': str(e),
                    'error_position': e.pos if hasattr(e, 'pos') else None
                }
                
        except Exception as e:
            return {
                'error': f'LLM extraction failed: {str(e)}'
            }
    
    def _build_extraction_prompt(self, raw_tables: List[Dict], service_config: Dict[str, Any]) -> str:
        """
        Build the extraction prompt for the LLM
        
        Args:
            raw_tables: Raw table data from HTML parsing
            service_config: Service configuration with extraction instructions
            
        Returns:
            str: Complete prompt for LLM extraction
        """
        
        # Convert raw tables to text format
        tables_text = ""
        for i, table_info in enumerate(raw_tables):
            table_data = table_info['data']
            tables_text += f"\n=== Table {i+1} ({table_info['type']}) ===\n"
            tables_text += f"Headers: {table_data['headers']}\n"
            tables_text += "Rows:\n"
            for row in table_data['rows']:
                tables_text += f"  {row}\n"
        
        # Build the prompt using the successful pattern from our testing
        prompt = f"""Extract {service_config['name']} information from this table data.

{service_config['extraction_focus']}

TABLE DATA:
{tables_text}

Return JSON format:
{{
  "items": [
    {{
      {', '.join([f'"{key}": "{desc}"' for key, desc in service_config['item_properties'].items()])}
    }}
  ]
}}"""
        
        return prompt
    
    def _call_bedrock_llm(self, prompt: str) -> str:
        """
        Call Bedrock LLM with the extraction prompt
        
        Args:
            prompt: Complete extraction prompt
            
        Returns:
            str: LLM response text
        """
        
        model_id = "global.amazon.nova-2-lite-v1:0"
        
        # Prepare the request body for Nova Lite
        request_body = {
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": prompt}]
                }
            ],
            "inferenceConfig": {
                "maxTokens": 4000,
                "temperature": 0.1,
                "topP": 0.9
            }
        }
        
        try:
            response = self.bedrock.converse(
                modelId=model_id,
                **request_body
            )
            
            # Extract the response text
            response_text = response['output']['message']['content'][0]['text']
            return response_text
            
        except ClientError as e:
            print(f"Bedrock API error: {e}")
            raise
    
