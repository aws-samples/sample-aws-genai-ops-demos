import { useState, useRef, useEffect, useCallback } from 'react';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Icon from '@cloudscape-design/components/icon';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Spinner from '@cloudscape-design/components/spinner';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Textarea from '@cloudscape-design/components/textarea';
import { invokeAgent } from '../agentcore';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------
interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  incomplete?: boolean;
}

type ConnectionStatus = 'connected' | 'disconnected' | 'reconnecting';

export interface ChatInterfaceProps {
  agentRuntimeArn: string;
  idToken: string;
  region: string;
  accountContext?: string;
  /** Called when the session expires and user needs to re-authenticate */
  onSessionExpired?: () => void;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const INITIAL_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30_000;
const BACKOFF_MULTIPLIER = 2;

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------
export default function ChatInterface({
  idToken,
  accountContext,
  onSessionExpired,
}: ChatInterfaceProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('connected');

  const backoffRef = useRef(INITIAL_BACKOFF_MS);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    return () => {
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    };
  }, []);

  // -------------------------------------------------------------------------
  // Send message via AgentCore SDK
  // -------------------------------------------------------------------------
  const sendMessage = useCallback(
    async (prompt: string, isRetry = false) => {
      if (!prompt.trim()) return;

      if (!isRetry) {
        setMessages((prev) => [
          ...prev,
          { role: 'user', content: prompt.trim(), timestamp: new Date().toISOString() },
          { role: 'assistant', content: '', timestamp: new Date().toISOString() },
        ]);
        setInputValue('');
        backoffRef.current = INITIAL_BACKOFF_MS;
      }

      setIsStreaming(true);
      setConnectionStatus('connected');

      try {
        const result = await invokeAgent({
          prompt: prompt.trim(),
          idToken,
          accountContext: accountContext || undefined,
          onChunk: (chunk) => {
            setMessages((prev) => {
              const updated = [...prev];
              const last = updated[updated.length - 1];
              if (last?.role === 'assistant') {
                updated[updated.length - 1] = {
                  ...last,
                  content: last.content + chunk,
                };
              }
              return updated;
            });
          },
        });

        // If no chunks were streamed, set the full response
        setMessages((prev) => {
          const updated = [...prev];
          const last = updated[updated.length - 1];
          if (last?.role === 'assistant' && !last.content) {
            updated[updated.length - 1] = { ...last, content: result.response };
          }
          return updated;
        });

        setConnectionStatus('connected');
        backoffRef.current = INITIAL_BACKOFF_MS;
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : 'Unknown error';

        // If session expired, trigger re-authentication instead of retrying
        if (msg.includes('Session expired') || msg.includes('sign in')) {
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last?.role === 'assistant') {
              updated[updated.length - 1] = {
                ...last,
                incomplete: true,
                content: 'Session expired. Please sign in again.',
              };
            }
            return updated;
          });
          setConnectionStatus('disconnected');
          onSessionExpired?.();
          return;
        }

        setMessages((prev) => {
          const updated = [...prev];
          const last = updated[updated.length - 1];
          if (last?.role === 'assistant') {
            updated[updated.length - 1] = {
              ...last,
              incomplete: true,
              content: last.content || `Error: ${msg}`,
            };
          }
          return updated;
        });

        // Schedule reconnect with backoff
        setConnectionStatus('reconnecting');
        const delay = Math.min(backoffRef.current, MAX_BACKOFF_MS);
        reconnectTimerRef.current = setTimeout(() => {
          backoffRef.current = Math.min(backoffRef.current * BACKOFF_MULTIPLIER, MAX_BACKOFF_MS);
          sendMessage(prompt, true);
        }, delay);
      } finally {
        setIsStreaming(false);
      }
    },
    [idToken, accountContext],
  );

  const handleSubmit = () => {
    if (isStreaming || !inputValue.trim()) return;
    sendMessage(inputValue);
  };

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------
  return (
    <SpaceBetween size="l">
      <Box float="right">
        {connectionStatus === 'connected' && (
          <StatusIndicator type="success">Connected</StatusIndicator>
        )}
        {connectionStatus === 'disconnected' && (
          <StatusIndicator type="error">Disconnected</StatusIndicator>
        )}
        {connectionStatus === 'reconnecting' && (
          <StatusIndicator type="in-progress">Reconnecting…</StatusIndicator>
        )}
      </Box>

      <div style={{ maxHeight: '60vh', overflowY: 'auto', padding: '8px 0' }}>
        <SpaceBetween size="m">
          {messages.length === 0 && (
            <Box textAlign="center" color="text-body-secondary" padding="xxl">
              Ask questions about your AWS operational data. The orchestration
              agent will route your query to the appropriate sub-agents.
            </Box>
          )}

          {messages.map((msg, idx) => (
            <div
              key={idx}
              style={{
                display: 'flex',
                justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
                gap: '8px',
              }}
            >
              {msg.role === 'assistant' && (
                <div style={{
                  width: 32, height: 32, borderRadius: '50%', background: '#0972d3',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  flexShrink: 0, color: '#fff', fontSize: 14, fontWeight: 600,
                }}>AI</div>
              )}

              <Container>
                <SpaceBetween size="xxs">
                  <Box variant="small" color="text-body-secondary">
                    {msg.role === 'user' ? 'You' : 'G.O.A.T.'} ·{' '}
                    {new Date(msg.timestamp).toLocaleTimeString()}
                  </Box>
                  <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                    {msg.content}
                  </div>
                  {msg.incomplete && (
                    <StatusIndicator type="warning">
                      Response incomplete — stream interrupted
                    </StatusIndicator>
                  )}
                  {isStreaming && idx === messages.length - 1 &&
                    msg.role === 'assistant' && !msg.incomplete && <Spinner size="normal" />}
                </SpaceBetween>
              </Container>

              {msg.role === 'user' && (
                <div style={{
                  width: 32, height: 32, borderRadius: '50%', background: '#414d5c',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  flexShrink: 0, color: '#fff', fontSize: 14,
                }}>
                  <Icon name="user-profile" />
                </div>
              )}
            </div>
          ))}
          <div ref={messagesEndRef} />
        </SpaceBetween>
      </div>

      <div
        style={{ display: 'flex', gap: '8px', alignItems: 'flex-end' }}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSubmit(); }
        }}
      >
        <div style={{ flex: 1 }}>
          <Textarea
            value={inputValue}
            onChange={({ detail }) => setInputValue(detail.value)}
            placeholder="Ask about your AWS operational data…"
            rows={2}
            disabled={isStreaming}
          />
        </div>
        <Button
          variant="primary"
          iconName="send"
          onClick={handleSubmit}
          loading={isStreaming}
          disabled={!inputValue.trim() || isStreaming}
        >
          Send
        </Button>
      </div>
    </SpaceBetween>
  );
}
