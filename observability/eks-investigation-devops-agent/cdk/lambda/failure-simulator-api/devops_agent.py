"""
Read-only calls to the AWS DevOps Agent data plane (usage, recent tasks of the Agent Space).

The Lambda runtime's boto3 has no service model for the agent yet, so requests are
SigV4-signed by hand. All data-plane APIs live under dp.aidevops.<region>.api.aws.
"""

import json
import logging
import os
import re
import urllib.request
from typing import Any, Dict, Optional

import boto3

logger = logging.getLogger(__name__)

DEVOPS_AGENT_REGION = os.environ.get('DEVOPS_AGENT_REGION') or os.environ.get('AWS_REGION', '')
AGENT_SPACE_ID = os.environ.get('DEVOPS_AGENT_SPACE_ID', '')
SKILL_PATH_RE = re.compile(r'/aidevops/skills/\w+/([^/]+)/SKILL\.md')


def _call(method: str, path: str, body: Optional[str] = None) -> Dict[str, Any]:
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    url = f'https://dp.aidevops.{DEVOPS_AGENT_REGION}.api.aws{path}'
    credentials = boto3.Session().get_credentials().get_frozen_credentials()
    headers = {'Content-Type': 'application/json'} if body else {}
    request = AWSRequest(method=method, url=url, headers=headers, data=body)
    SigV4Auth(credentials, 'aidevops', DEVOPS_AGENT_REGION).add_auth(request)
    req = urllib.request.Request(url, headers=dict(request.headers), method=method,
                                 data=body.encode('utf-8') if body else None)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode('utf-8'))


def get_usage() -> Dict[str, Any]:
    try:
        return {'success': True, **_call('GET', '/usage/account')}
    except Exception as e:
        logger.warning('DevOps Agent usage failed: %s', e)
        return {'success': False, 'message': str(e)}


def _execution_details(task_id: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    exec_data = _call('POST', f'/journal/agent-space/{AGENT_SPACE_ID}/executions',
                      json.dumps({'taskId': task_id, 'limit': 1}))
    executions = exec_data.get('executions', [])
    if not executions:
        return out
    exe = executions[0]
    out.update({
        'executionId': exe.get('executionId', ''),
        'executionStatus': exe.get('executionStatus', ''),
        'agentType': exe.get('agentType', ''),
        'executionCreatedAt': exe.get('createdAt', ''),
        'executionUpdatedAt': exe.get('updatedAt', ''),
    })
    exec_id = exe.get('executionId')
    if not exec_id:
        return out
    records = _call('POST', f'/journal/agent-space/{AGENT_SPACE_ID}/journalRecords',
                    json.dumps({'executionId': exec_id, 'limit': 100, 'order': 'DESC'})).get('records', [])
    tool_calls, skill_names, summary_md = 0, [], ''
    for rec in records:
        rt, content = rec.get('recordType', ''), rec.get('content', '')
        text = str(content)
        if rt == 'message' and 'tool_use' in text:
            tool_calls += 1
            for name in SKILL_PATH_RE.findall(text):
                if name not in skill_names:
                    skill_names.append(name)
        elif rt == 'investigation_summary_md' and not summary_md and isinstance(content, str):
            summary_md = content
    out.update({
        'toolCalls': tool_calls,
        'skillReads': len(skill_names),
        'skillNames': skill_names,
        'summaryMd': summary_md[:3000],
        'journalRecordCount': len(records),
    })
    return out


def get_tasks() -> Dict[str, Any]:
    """Ten most recent tasks with their own execution (LINKED tasks have none)."""
    if not AGENT_SPACE_ID:
        return {'success': False, 'message': 'DEVOPS_AGENT_SPACE_ID not configured'}
    try:
        tasks_data = _call('POST', f'/backlog/agent-space/{AGENT_SPACE_ID}/tasks/list',
                           json.dumps({'limit': 20, 'sortField': 'CREATED_AT', 'order': 'DESC'}))
        tasks = [t for t in tasks_data.get('tasks', []) if t.get('status') != 'LINKED'][:10]
        tasks_out = []
        for task in tasks:
            task_id = task.get('taskId', '')
            entry = {k: task.get(k, '') for k in ('taskId', 'title', 'status', 'priority', 'taskType', 'createdAt', 'updatedAt')}
            try:
                entry.update(_execution_details(task_id))
            except Exception as e:
                logger.warning('Execution details failed for task %s: %s', task_id, e)
            tasks_out.append(entry)
        return {'success': True, 'tasks': tasks_out}
    except Exception as e:
        logger.warning('DevOps Agent tasks failed: %s', e)
        return {'success': False, 'message': str(e)}
