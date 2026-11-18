import os
import json
import time
import boto3
import logging
from kubernetes import client, config, watch

# ─────────────────────────────────────────
# LOGGING SETUP
# ─────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────
# CONFIG (from Helm values → env vars)
# ─────────────────────────────────────────
AWS_REGION          = os.getenv('AWS_REGION') or os.getenv('AWS_DEFAULT_REGION')
BEDROCK_MODEL       = os.getenv('BEDROCK_MODEL', 'anthropic.claude-3-5-sonnet-20241022-v2:0')
SKIP_NAMESPACES     = os.getenv('SKIP_NAMESPACES', 'kube-system,kube-public').split(',')
RESTART_THRESHOLD   = int(os.getenv('RESTART_THRESHOLD', '3'))

APPROVE_ANNOTATION  = 'eks-agent/approve'
ANALYZED_ANNOTATION = 'eks-agent/analyzed'

# Track pods we already analyzed — don't spam Bedrock
analyzed_pods = set()


# ─────────────────────────────────────────
# KUBERNETES CONFIG
# ─────────────────────────────────────────
def load_k8s_config():
    try:
        config.load_incluster_config()      # ← Running as a pod inside EKS
        log.info("✅ Loaded in-cluster config")
    except Exception:
        config.load_kube_config()            # ← Running locally for dev/test
        log.info("✅ Loaded local kubeconfig")


# ─────────────────────────────────────────
# SIGNAL COLLECTION
# ─────────────────────────────────────────
def get_pod_logs(namespace, pod_name, container_name, core_v1):
    try:
        return core_v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container_name,
            previous=True,
            tail_lines=80
        )
    except Exception as e:
        return f"Could not fetch logs: {e}"


def get_pod_detail(namespace, pod_name, core_v1):
    """Get resource limits and recent events for this pod"""
    try:
        pod = core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)

        # Extract container resource config
        containers = []
        for c in pod.spec.containers:
            req = c.resources.requests or {}
            lim = c.resources.limits   or {}
            containers.append({
                'name':           c.name,
                'image':          c.image,
                'memory_request': req.get('memory', 'not set'),
                'memory_limit':   lim.get('memory', 'not set'),
                'cpu_request':    req.get('cpu',    'not set'),
            })

        # Get recent events
        events = core_v1.list_namespaced_event(
            namespace=namespace,
            field_selector=f'involvedObject.name={pod_name}'
        )
        event_lines = [
            f"  {e.reason}: {e.message}"
            for e in events.items[-8:]
        ]

        return {
            'containers':  containers,
            'events':      event_lines,
            'node':        pod.spec.node_name,
            'owner_refs':  pod.metadata.owner_references or [],
        }

    except Exception as e:
        return {'error': str(e), 'containers': [], 'events': [], 'node': 'unknown', 'owner_refs': []}


def find_deployment_for_pod(namespace, pod_name, pod_detail, core_v1, apps_v1):
    """Walk up: Pod → ReplicaSet → Deployment"""
    try:
        for ref in pod_detail.get('owner_refs', []):
            if ref.kind == 'ReplicaSet':
                rs = apps_v1.read_namespaced_replica_set(name=ref.name, namespace=namespace)
                for rs_ref in (rs.metadata.owner_references or []):
                    if rs_ref.kind == 'Deployment':
                        return rs_ref.name
    except Exception as e:
        log.warning(f"Could not find Deployment: {e}")
    return None


# ─────────────────────────────────────────
# BUILD INCIDENT PACKAGE
# ─────────────────────────────────────────
def build_incident_package(pod_info, pod_detail, extra_signal, signal_type):
    containers_text = "\n".join([
        f"  {c['name']}: memory request={c['memory_request']}, limit={c['memory_limit']}"
        for c in pod_detail.get('containers', [])
    ])
    events_text = "\n".join(pod_detail.get('events', ['No events found']))

    return f"""
====================================
EKS INCIDENT PACKAGE
====================================
Pod Name:      {pod_info['pod']}
Namespace:     {pod_info['namespace']}
Container:     {pod_info['container']}
Restart Count: {pod_info['restart_count']}
Exit Code:     {pod_info['exit_code']}
OOMKilled:     {pod_info['oom_killed']}
Node:          {pod_detail.get('node', 'unknown')}
Status:        CrashLoopBackOff

---- CONTAINER RESOURCES ----
{containers_text}

---- RECENT EVENTS ----
{events_text}

---- {signal_type} ----
{extra_signal}
====================================
"""


# ─────────────────────────────────────────
# BEDROCK RCA
# ─────────────────────────────────────────
def _extract_json(text):
    """Return the JSON object in a model reply.

    Newer Claude models often wrap JSON in a ```json ... ``` fence even when
    asked for JSON only. Take the span from the first '{' to the last '}' so
    both fenced and bare replies parse.
    """
    start, end = text.find('{'), text.rfind('}')
    if start == -1 or end < start:
        return text
    return text[start:end + 1]


def analyze_with_bedrock(incident_package, exit_code, current_memory_limit):
    bedrock = boto3.client('bedrock-runtime', region_name=AWS_REGION)

    prompt = f"""You are an expert EKS engineer. Analyze this pod failure.

Return your response as valid JSON only. No extra text. Use this exact structure:
{{
  "root_cause": "one clear sentence",
  "evidence": ["point 1", "point 2", "point 3"],
  "proposed_fix": "exact actionable fix",
  "new_memory_limit": "only if exit_code is 137, suggest a specific value like 512Mi or 1Gi, else null",
  "confidence": "High or Medium or Low",
  "priority": "High or Medium or Low"
}}

INCIDENT:
{incident_package}
"""

    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 600,
        "messages": [{"role": "user", "content": prompt}]
    }

    # ── LOG THE FULL REQUEST ────────────────
    log.info("=" * 60)
    log.info("📤 BEDROCK REQUEST")
    log.info("=" * 60)
    log.info(f"  Model:   {BEDROCK_MODEL}")
    log.info(f"  Region:  {AWS_REGION}")
    log.info("  Prompt:")
    log.info(f"{prompt}")
    log.info("=" * 60)

    response = bedrock.invoke_model(
        modelId=BEDROCK_MODEL,
        body=json.dumps(request_body)
    )

    raw = json.loads(response['body'].read())['content'][0]['text']

    # ── LOG THE FULL RESPONSE ───────────────
    log.info("=" * 60)
    log.info("📥 BEDROCK RAW RESPONSE")
    log.info("=" * 60)
    log.info(raw)
    log.info("=" * 60)

    try:
        parsed = json.loads(_extract_json(raw))

        log.info("📥 BEDROCK PARSED RESPONSE")
        log.info("=" * 60)
        log.info(json.dumps(parsed, indent=2))
        log.info("=" * 60)

        return parsed

    except Exception:
        return {
            "root_cause":       raw,
            "evidence":         [],
            "proposed_fix":     "Review Bedrock output above",
            "new_memory_limit": None,
            "confidence":       "Low",
            "priority":         "Medium"
        }
    
# ─────────────────────────────────────────
# FIX: PATCH DEPLOYMENT MEMORY
# ─────────────────────────────────────────
def patch_deployment_memory(namespace, deployment_name, container_name, new_limit, apps_v1):
    try:
        deployment = apps_v1.read_namespaced_deployment(
            name=deployment_name,
            namespace=namespace
        )

        for container in deployment.spec.template.spec.containers:
            if container.name == container_name:
                if not container.resources.limits:
                    container.resources.limits = {}
                container.resources.limits['memory'] = new_limit
                log.info(f"   Setting memory limit → {new_limit}")

        apps_v1.patch_namespaced_deployment(
            name=deployment_name,
            namespace=namespace,
            body=deployment
        )
        log.info(f"✅ Deployment '{deployment_name}' patched successfully")
        return True

    except Exception as e:
        log.error(f"❌ Failed to patch deployment: {e}")
        return False


# ─────────────────────────────────────────
# ASSIST MODE: WAIT FOR APPROVAL
# ─────────────────────────────────────────
def wait_for_approval(namespace, pod_name, core_v1):
    """
    Watch for operator to annotate the pod with eks-agent/approve=true or false
    Times out after 10 minutes
    """
    log.info(f"\n  ⏳ WAITING FOR YOUR DECISION (timeout: 10 min)")
    log.info(f"  ✅ To APPROVE: kubectl annotate pod {pod_name} -n {namespace} {APPROVE_ANNOTATION}=true --overwrite")
    log.info(f"  ❌ To REJECT:  kubectl annotate pod {pod_name} -n {namespace} {APPROVE_ANNOTATION}=false --overwrite")

    w = watch.Watch()
    for event in w.stream(
        core_v1.list_namespaced_pod,
        namespace=namespace,
        timeout_seconds=600           # 10 minutes
    ):
        p = event['object']
        if p.metadata.name != pod_name:
            continue

        annotations = p.metadata.annotations or {}
        decision    = annotations.get(APPROVE_ANNOTATION)

        if decision == 'true':
            log.info("✅ Operator APPROVED the fix")
            w.stop()
            return True

        if decision == 'false':
            log.info("❌ Operator REJECTED the fix — no action taken")
            w.stop()
            return False

    log.info("⏰ Approval window expired — no action taken")
    return False


# ─────────────────────────────────────────
# MAIN PROCESSING LOGIC
# ─────────────────────────────────────────
def process_pod(pod_info, core_v1, apps_v1):
    pod_key = f"{pod_info['namespace']}/{pod_info['pod']}"

    if pod_key in analyzed_pods:
        return                          # Already handled this pod

    analyzed_pods.add(pod_key)

    log.info(f"\n{'='*60}")
    log.info(f"🚨 INCIDENT DETECTED")
    log.info(f"   Pod:       {pod_info['pod']}")
    log.info(f"   Namespace: {pod_info['namespace']}")
    log.info(f"   Restarts:  {pod_info['restart_count']}")
    log.info(f"   Exit Code: {pod_info['exit_code']}")
    log.info(f"{'='*60}")

    pod_detail          = get_pod_detail(pod_info['namespace'], pod_info['pod'], core_v1)
    exit_code           = pod_info['exit_code']
    current_mem_limit   = None

    # ── Route by exit code ──────────────────
    if exit_code == 137 or pod_info['oom_killed']:
        log.info("💾 Exit Code 137 → OOMKilled path")
        for c in pod_detail.get('containers', []):
            if c['name'] == pod_info['container']:
                current_mem_limit = c.get('memory_limit', 'not set')
        extra_signal = f"Memory Limit: {current_mem_limit}\nReason: OOMKilled"
        signal_type  = "MEMORY INFO"

    elif exit_code == 1:
        log.info("🔴 Exit Code 1 → Application error path")
        extra_signal = get_pod_logs(
            pod_info['namespace'],
            pod_info['pod'],
            pod_info['container'],
            core_v1
        )
        signal_type  = "POD LOGS (last 80 lines)"

    else:
        log.info(f"❓ Exit Code {exit_code} → No specific handler")
        extra_signal = f"Exit code {exit_code} has no specific handler yet"
        signal_type  = "NO ADDITIONAL SIGNAL"

    # ── Build package + call Bedrock ────────
    incident  = build_incident_package(pod_info, pod_detail, extra_signal, signal_type)

    log.info("\n🤖 Contacting Amazon Bedrock for root cause analysis...")
    diagnosis = analyze_with_bedrock(incident, exit_code, current_mem_limit)

    # ── Print clean diagnosis ───────────────
    log.info(f"\n{'─'*60}")
    log.info("📊 BEDROCK DIAGNOSIS")
    log.info(f"{'─'*60}")
    log.info(f"  ROOT CAUSE:    {diagnosis.get('root_cause')}")
    log.info(f"  CONFIDENCE:    {diagnosis.get('confidence')}")
    log.info(f"  PRIORITY:      {diagnosis.get('priority')}")
    log.info(f"  EVIDENCE:")
    for e in diagnosis.get('evidence', []):
        log.info(f"    • {e}")
    log.info(f"  PROPOSED FIX:  {diagnosis.get('proposed_fix')}")
    log.info(f"{'─'*60}")

    # ── ASSIST MODE: OOM fix with approval ──
    if exit_code == 137 or pod_info['oom_killed']:
        new_limit        = diagnosis.get('new_memory_limit') or _double_memory(current_mem_limit)
        deployment_name  = find_deployment_for_pod(
            pod_info['namespace'], pod_info['pod'], pod_detail, core_v1, apps_v1
        )

        if not deployment_name:
            log.warning("⚠️  No Deployment found — cannot auto-patch. Manual fix required.")
            return

        log.info(f"\n🔧 PROPOSED ACTION:")
        log.info(f"   Increase memory limit on Deployment '{deployment_name}'")
        log.info(f"   {current_mem_limit}  →  {new_limit}")

        approved = wait_for_approval(pod_info['namespace'], pod_info['pod'], core_v1)

        if approved:
            patch_deployment_memory(
                pod_info['namespace'],
                deployment_name,
                pod_info['container'],
                new_limit,
                apps_v1
            )

    else:
        # Exit code 1 — no auto-fix, diagnosis is the value
        log.info("💡 ASSIST MODE: No automatic fix for exit code 1")
        log.info("   Diagnosis above contains recommended steps")
        log.info("   Manual intervention required")


def _double_memory(memory_str):
    """Simple helper: double the memory value as a fallback suggestion"""
    if not memory_str or memory_str == 'not set':
        return '512Mi'
    try:
        if 'Mi' in memory_str:
            value = int(memory_str.replace('Mi', ''))
            return f"{value * 2}Mi"
        if 'Gi' in memory_str:
            value = int(memory_str.replace('Gi', ''))
            return f"{value * 2}Gi"
    except Exception:
        pass
    return '512Mi'


# ─────────────────────────────────────────
# CONTINUOUS WATCHER
# ─────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("  AGATHA  |  EKS Self-Healing Agent  |  ASSIST MODE  |  POC v0.1")
    log.info("=" * 60)

    load_k8s_config()
    core_v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()

    log.info(f"👁️  Watching all namespaces (skipping: {SKIP_NAMESPACES})")
    log.info(f"⚡ Trigger after {RESTART_THRESHOLD}+ restarts")
    log.info("─" * 60)

    while True:
        try:
            log.info("🔄 Starting Kubernetes watch stream...")
            w = watch.Watch()

            for event in w.stream(
                core_v1.list_pod_for_all_namespaces,
                timeout_seconds=60      # Reconnect every 60s (normal k8s practice)
            ):
                pod       = event['object']
                namespace = pod.metadata.namespace
                pod_name  = pod.metadata.name

                if namespace in SKIP_NAMESPACES:
                    continue

                for cs in (pod.status.container_statuses or []):
                    waiting = cs.state.waiting if cs.state else None

                    if not waiting or waiting.reason != 'CrashLoopBackOff':
                        continue

                    if cs.restart_count < RESTART_THRESHOLD:
                        continue

                    last        = cs.last_state.terminated if cs.last_state else None
                    exit_code   = last.exit_code if last else -1
                    oom_killed  = (last.reason == 'OOMKilled') if last else False

                    process_pod({
                        'namespace':     namespace,
                        'pod':           pod_name,
                        'container':     cs.name,
                        'exit_code':     exit_code,
                        'oom_killed':    oom_killed,
                        'restart_count': cs.restart_count,
                    }, core_v1, apps_v1)

        except Exception as e:
            log.error(f"Watch error: {e} — restarting in 10s...")
            time.sleep(10)


if __name__ == "__main__":
    main()