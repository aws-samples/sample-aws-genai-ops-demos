"""Test file with Nova model and large prompt."""

import boto3

# Using Nova Lite
bedrock = boto3.client('bedrock-runtime')

def process_document(doc_content):
    """Process a document with Nova Lite."""
    
    # Large system prompt without caching
    system_prompt = """
    You are an expert document analyzer. Your task is to extract key information
    from documents and provide structured summaries.
    
    Follow these guidelines:
    1. Read the document carefully
    2. Identify main topics and themes
    3. Extract key facts and figures
    4. Summarize in a structured format
    5. Highlight any important dates or deadlines
    6. Note any action items or recommendations
    
    Always be precise and factual. Do not make assumptions or add information
    that is not present in the document. If something is unclear, note it as such.
    
    Your output should be in JSON format with the following structure:
    {
        "summary": "Brief overview",
        "key_topics": ["topic1", "topic2"],
        "facts": ["fact1", "fact2"],
        "dates": ["date1", "date2"],
        "action_items": ["item1", "item2"]
    }
    """
    
    response = bedrock.invoke_model(
        modelId="amazon.nova-lite-v1:0",
        body={
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": f"Analyze this document: {doc_content}"
                }
            ]
        }
    )
    
    return response
