import { useState } from 'react';
import AppLayout from '@cloudscape-design/components/app-layout';
import TopNavigation from '@cloudscape-design/components/top-navigation';
import ContentLayout from '@cloudscape-design/components/content-layout';
import Container from '@cloudscape-design/components/container';
import SpaceBetween from '@cloudscape-design/components/space-between';
import Box from '@cloudscape-design/components/box';
import ButtonGroup from '@cloudscape-design/components/button-group';
import Grid from '@cloudscape-design/components/grid';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import ChatBubble from '@cloudscape-design/chat-components/chat-bubble';
import Avatar from '@cloudscape-design/chat-components/avatar';
import SupportPromptGroup from '@cloudscape-design/chat-components/support-prompt-group';
import PromptInput from '@cloudscape-design/components/prompt-input';
import Alert from '@cloudscape-design/components/alert';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { invokeAgent } from './agentcore';
import './markdown.css';

interface Message {
  type: 'user' | 'agent';
  content: string;
  timestamp: Date;
}

interface MessageFeedback {
  [messageIndex: number]: {
    feedback?: 'helpful' | 'not-helpful';
    submitting?: boolean;
    showCopySuccess?: boolean;
  };
}

function App() {
  const isLocalDev = (import.meta as any).env.VITE_LOCAL_DEV === 'true';
  const [prompt, setPrompt] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [messageFeedback, setMessageFeedback] = useState<MessageFeedback>({});
  const [showSupportPrompts, setShowSupportPrompts] = useState(true);
  
  // Generate a unique session ID for this conversation
  const [sessionId] = useState(() => `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`);

  const handleFeedback = async (messageIndex: number, feedbackType: 'helpful' | 'not-helpful') => {
    setMessageFeedback(prev => ({ ...prev, [messageIndex]: { submitting: true } }));
    await new Promise(resolve => setTimeout(resolve, 500));
    setMessageFeedback(prev => ({ ...prev, [messageIndex]: { feedback: feedbackType, submitting: false } }));
  };


  const handleCopy = async (messageIndex: number, content: string) => {
    try {
      await navigator.clipboard.writeText(content);
      setMessageFeedback(prev => ({ ...prev, [messageIndex]: { ...prev[messageIndex], showCopySuccess: true } }));
      setTimeout(() => {
        setMessageFeedback(prev => ({ ...prev, [messageIndex]: { ...prev[messageIndex], showCopySuccess: false } }));
      }, 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  const cleanResponse = (response: string): string => {
    let cleaned = response.trim();
    if ((cleaned.startsWith('"') && cleaned.endsWith('"')) || (cleaned.startsWith("'") && cleaned.endsWith("'"))) {
      cleaned = cleaned.slice(1, -1);
    }
    return cleaned.replace(/\\n/g, '\n').replace(/\\t/g, '\t');
  };

  const handleSupportPromptClick = (promptText: string) => {
    setPrompt(promptText);
    setShowSupportPrompts(false);
  };

  const handleSendMessage = async () => {
    if (!prompt.trim()) {
      setError('Please enter a message');
      return;
    }

    setShowSupportPrompts(false);
    const userMessage: Message = { type: 'user', content: prompt, timestamp: new Date() };
    setMessages(prev => [...prev, userMessage]);
    setLoading(true);
    setError('');
    const currentPrompt = prompt;
    setPrompt('');

    const streamingMessageIndex = messages.length + 1;
    setMessages(prev => [...prev, { type: 'agent', content: '', timestamp: new Date() }]);

    try {
      let streamedContent = '';
      
      const data = await invokeAgent({
        prompt: currentPrompt,
        sessionId,
        onChunk: (chunk: string) => {
          streamedContent += chunk;
          setMessages(prev => {
            const updated = [...prev];
            updated[streamingMessageIndex] = { type: 'agent', content: streamedContent, timestamp: new Date() };
            return updated;
          });
        }
      });

      const finalContent = cleanResponse(data.response || streamedContent);
      setMessages(prev => {
        const updated = [...prev];
        updated[streamingMessageIndex] = { type: 'agent', content: finalContent, timestamp: new Date() };
        return updated;
      });
      setShowSupportPrompts(true);
    } catch (err: any) {
      setError(err.message);
      setMessages(prev => prev.slice(0, -1));
    } finally {
      setLoading(false);
    }
  };


  // Password reset specific prompts
  const getSupportPrompts = () => {
    if (messages.length === 0) {
      return [
        { id: 'forgot', text: 'I forgot my password' },
        { id: 'reset', text: 'I need to reset my password' },
        { id: 'locked', text: "I'm locked out of my account" },
        { id: 'help', text: 'How does password reset work?' }
      ];
    }

    const lastMessage = messages[messages.length - 1];
    if (lastMessage.type === 'agent') {
      const content = lastMessage.content.toLowerCase();
      
      if (content.includes('email') && content.includes('check')) {
        return [
          { id: 'received', text: 'I received the code' },
          { id: 'no-email', text: "I didn't receive an email" },
          { id: 'resend', text: 'Please send a new code' }
        ];
      }
      
      if (content.includes('verification code') || content.includes('enter the code')) {
        return [
          { id: 'wrong-code', text: 'The code is not working' },
          { id: 'expired', text: 'My code expired' },
          { id: 'new-code', text: 'Send me a new code' }
        ];
      }
      
      if (content.includes('password') && content.includes('requirement')) {
        return [
          { id: 'ready', text: "I'm ready to set my new password" },
          { id: 'help-pw', text: 'What makes a strong password?' }
        ];
      }
      
      if (content.includes('success') || content.includes('reset')) {
        return [
          { id: 'thanks', text: 'Thank you!' },
          { id: 'another', text: 'I need to reset another account' }
        ];
      }
    }

    return [
      { id: 'start-over', text: 'Start over' },
      { id: 'help-general', text: 'I need help' }
    ];
  };


  return (
    <>
      <TopNavigation
        identity={{
          href: "#",
          title: isLocalDev ? "Password Reset Assistant (Local Dev)" : "Password Reset Assistant"
        }}
        utilities={isLocalDev ? [{ type: "button", text: "Local Development", iconName: "settings" }] : []}
        i18nStrings={{ overflowMenuTriggerText: "More", overflowMenuTitleText: "All" }}
      />
      <AppLayout
        navigationHide={true}
        toolsHide={true}
        disableContentPaddings
        contentType="default"
        content={
          <ContentLayout defaultPadding>
            <Grid gridDefinition={[
              { colspan: { default: 12, xs: 1, s: 2 } },
              { colspan: { default: 12, xs: 10, s: 8 } },
              { colspan: { default: 12, xs: 1, s: 2 } }
            ]}>
              <div />
              <SpaceBetween size="l">
                {error && (
                  <Alert type="error" dismissible onDismiss={() => setError('')}>{error}</Alert>
                )}
                <Container>
                  <div role="region" aria-label="Chat">
                    <SpaceBetween size="m">
                      {messages.length === 0 ? (
                        <Box textAlign="center" padding={{ vertical: 'xxl' }} color="text-body-secondary">
                          <Box variant="h2" margin={{ bottom: 's' }}>Password Reset Assistant</Box>
                          <Box>Need help resetting your password? I can guide you through the process securely.</Box>
                        </Box>
                      ) : (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                          {messages.map((message, index) => {
                            const feedback = messageFeedback[index];
                            const isAgent = message.type === 'agent';
                            return (
                              <div key={index} style={{ display: 'flex', gap: '12px', alignItems: 'flex-start' }}>
                                {isAgent && (
                                  <Avatar ariaLabel="Assistant" tooltipText="Password Reset Assistant" iconName="gen-ai" color="gen-ai" />
                                )}
                                <div style={{ flex: 1 }}>
                                  <ChatBubble
                                    type={message.type === 'user' ? 'outgoing' : 'incoming'}
                                    ariaLabel={`${message.type === 'user' ? 'You' : 'Assistant'}`}
                                    avatar={message.type === 'user' ? <div /> : undefined}
                                  >
                                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
                                  </ChatBubble>

                                  {isAgent && (
                                    <div style={{ marginTop: '8px' }}>
                                      <ButtonGroup
                                        variant="icon"
                                        ariaLabel="Message actions"
                                        items={[
                                          { type: 'icon-button', id: 'thumbs-up', iconName: feedback?.feedback === 'helpful' ? 'thumbs-up-filled' : 'thumbs-up', text: 'Helpful', disabled: feedback?.submitting || !!feedback?.feedback },
                                          { type: 'icon-button', id: 'thumbs-down', iconName: feedback?.feedback === 'not-helpful' ? 'thumbs-down-filled' : 'thumbs-down', text: 'Not helpful', disabled: feedback?.submitting || !!feedback?.feedback },
                                          { type: 'icon-button', id: 'copy', iconName: 'copy', text: 'Copy', popoverFeedback: feedback?.showCopySuccess ? <StatusIndicator type="success">Copied</StatusIndicator> : undefined }
                                        ]}
                                        onItemClick={({ detail }) => {
                                          if (detail.id === 'thumbs-up') handleFeedback(index, 'helpful');
                                          else if (detail.id === 'thumbs-down') handleFeedback(index, 'not-helpful');
                                          else if (detail.id === 'copy') handleCopy(index, message.content);
                                        }}
                                      />
                                    </div>
                                  )}
                                </div>
                              </div>
                            );
                          })}
                          {loading && (
                            <div style={{ display: 'flex', gap: '12px', alignItems: 'flex-start' }}>
                              <Avatar ariaLabel="Assistant" tooltipText="Password Reset Assistant" iconName="gen-ai" color="gen-ai" loading={true} />
                              <Box color="text-body-secondary">Processing your request...</Box>
                            </div>
                          )}
                        </div>
                      )}

                      {showSupportPrompts && !loading && (
                        <SupportPromptGroup
                          onItemClick={({ detail }) => handleSupportPromptClick(getSupportPrompts().find(p => p.id === detail.id)?.text || '')}
                          ariaLabel="Suggested prompts"
                          alignment="horizontal"
                          items={getSupportPrompts()}
                        />
                      )}

                      <PromptInput
                        value={prompt}
                        onChange={({ detail }) => setPrompt(detail.value)}
                        onAction={handleSendMessage}
                        placeholder="Type your message..."
                        actionButtonAriaLabel="Send message"
                        actionButtonIconName="send"
                        disabled={loading}
                      />
                    </SpaceBetween>
                  </div>
                </Container>
              </SpaceBetween>
              <div />
            </Grid>
          </ContentLayout>
        }
      />
    </>
  );
}

export default App;
