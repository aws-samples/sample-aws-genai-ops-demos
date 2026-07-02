"use strict";
/**
 * G.O.A.T. - GenAI Operations Analytics Tool
 * Shared constants used across CDK stacks, agents, and frontend
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.CONVERSATION_TTL_DAYS = exports.SUB_AGENT_TIMEOUT_SECONDS = exports.MODEL_ID_NOVA_LITE = exports.MODEL_ID_NOVA_PRO = exports.DOMAINS = void 0;
// ---------------------------------------------------------------------------
// Operational Domains
// ---------------------------------------------------------------------------
/** Supported sub-agent domains */
exports.DOMAINS = ['cost', 'health', 'support', 'trusted_advisor', 'cur', 'network'];
// ---------------------------------------------------------------------------
// Amazon Bedrock Model IDs
// ---------------------------------------------------------------------------
/** Nova Pro -- used by the Orchestration Agent for complex reasoning */
exports.MODEL_ID_NOVA_PRO = 'amazon.nova-pro-v1:0';
/** Nova Lite -- used by sub-agents for simpler retrieve-and-format tasks */
exports.MODEL_ID_NOVA_LITE = 'amazon.nova-lite-v1:0';
// ---------------------------------------------------------------------------
// Timeouts & TTLs
// ---------------------------------------------------------------------------
/** Maximum time (seconds) to wait for a sub-agent response */
exports.SUB_AGENT_TIMEOUT_SECONDS = 30;
/** Conversation TTL in days before archival */
exports.CONVERSATION_TTL_DAYS = 90;
//# sourceMappingURL=data:application/json;base64,eyJ2ZXJzaW9uIjozLCJmaWxlIjoiY29uc3RhbnRzLmpzIiwic291cmNlUm9vdCI6IiIsInNvdXJjZXMiOlsiY29uc3RhbnRzLnRzIl0sIm5hbWVzIjpbXSwibWFwcGluZ3MiOiI7QUFBQTs7O0dBR0c7OztBQUVILDhFQUE4RTtBQUM5RSxzQkFBc0I7QUFDdEIsOEVBQThFO0FBRTlFLGtDQUFrQztBQUNyQixRQUFBLE9BQU8sR0FBRyxDQUFDLE1BQU0sRUFBRSxRQUFRLEVBQUUsU0FBUyxFQUFFLGlCQUFpQixFQUFFLEtBQUssRUFBRSxTQUFTLENBQVUsQ0FBQztBQUduRyw4RUFBOEU7QUFDOUUsMkJBQTJCO0FBQzNCLDhFQUE4RTtBQUU5RSx3RUFBd0U7QUFDM0QsUUFBQSxpQkFBaUIsR0FBRyxzQkFBc0IsQ0FBQztBQUV4RCw0RUFBNEU7QUFDL0QsUUFBQSxrQkFBa0IsR0FBRyx1QkFBdUIsQ0FBQztBQUUxRCw4RUFBOEU7QUFDOUUsa0JBQWtCO0FBQ2xCLDhFQUE4RTtBQUU5RSw4REFBOEQ7QUFDakQsUUFBQSx5QkFBeUIsR0FBRyxFQUFFLENBQUM7QUFFNUMsK0NBQStDO0FBQ2xDLFFBQUEscUJBQXFCLEdBQUcsRUFBRSxDQUFDIiwic291cmNlc0NvbnRlbnQiOlsiLyoqXHJcbiAqIEcuTy5BLlQuIC0gR2VuQUkgT3BlcmF0aW9ucyBBbmFseXRpY3MgVG9vbFxyXG4gKiBTaGFyZWQgY29uc3RhbnRzIHVzZWQgYWNyb3NzIENESyBzdGFja3MsIGFnZW50cywgYW5kIGZyb250ZW5kXHJcbiAqL1xyXG5cclxuLy8gLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tXHJcbi8vIE9wZXJhdGlvbmFsIERvbWFpbnNcclxuLy8gLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tXHJcblxyXG4vKiogU3VwcG9ydGVkIHN1Yi1hZ2VudCBkb21haW5zICovXHJcbmV4cG9ydCBjb25zdCBET01BSU5TID0gWydjb3N0JywgJ2hlYWx0aCcsICdzdXBwb3J0JywgJ3RydXN0ZWRfYWR2aXNvcicsICdjdXInLCAnbmV0d29yayddIGFzIGNvbnN0O1xyXG5leHBvcnQgdHlwZSBEb21haW4gPSB0eXBlb2YgRE9NQUlOU1tudW1iZXJdO1xyXG5cclxuLy8gLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tXHJcbi8vIEFtYXpvbiBCZWRyb2NrIE1vZGVsIElEc1xyXG4vLyAtLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS1cclxuXHJcbi8qKiBOb3ZhIFBybyAtLSB1c2VkIGJ5IHRoZSBPcmNoZXN0cmF0aW9uIEFnZW50IGZvciBjb21wbGV4IHJlYXNvbmluZyAqL1xyXG5leHBvcnQgY29uc3QgTU9ERUxfSURfTk9WQV9QUk8gPSAnYW1hem9uLm5vdmEtcHJvLXYxOjAnO1xyXG5cclxuLyoqIE5vdmEgTGl0ZSAtLSB1c2VkIGJ5IHN1Yi1hZ2VudHMgZm9yIHNpbXBsZXIgcmV0cmlldmUtYW5kLWZvcm1hdCB0YXNrcyAqL1xyXG5leHBvcnQgY29uc3QgTU9ERUxfSURfTk9WQV9MSVRFID0gJ2FtYXpvbi5ub3ZhLWxpdGUtdjE6MCc7XHJcblxyXG4vLyAtLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS1cclxuLy8gVGltZW91dHMgJiBUVExzXHJcbi8vIC0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLVxyXG5cclxuLyoqIE1heGltdW0gdGltZSAoc2Vjb25kcykgdG8gd2FpdCBmb3IgYSBzdWItYWdlbnQgcmVzcG9uc2UgKi9cclxuZXhwb3J0IGNvbnN0IFNVQl9BR0VOVF9USU1FT1VUX1NFQ09ORFMgPSAzMDtcclxuXHJcbi8qKiBDb252ZXJzYXRpb24gVFRMIGluIGRheXMgYmVmb3JlIGFyY2hpdmFsICovXHJcbmV4cG9ydCBjb25zdCBDT05WRVJTQVRJT05fVFRMX0RBWVMgPSA5MDtcclxuIl19