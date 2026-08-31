import { useState, useEffect } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  Button,
  Badge,
  ColumnLayout,
  Spinner,
  Alert,
  Modal,
  FormField,
  Input,
  Textarea,
  Select,
  SelectProps,
  Tabs,
  KeyValuePairs,
  ExpandableSection,
  StatusIndicator,
} from '@cloudscape-design/components';
import { useParams, useNavigate } from 'react-router-dom';
import { UserInfo } from '../App';
import { Finding, PRIORITY_COLORS, STATUS_COLORS, REJECTION_CATEGORIES } from '../types';
import { getFinding, acceptFinding, rejectFinding } from '../services/api';

interface FindingDetailProps {
  userInfo: UserInfo | null;
}

export default function FindingDetail({ userInfo }: FindingDetailProps) {
  const { findingId } = useParams<{ findingId: string }>();
  const navigate = useNavigate();

  const [finding, setFinding] = useState<Finding | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  
  // Accept modal state
  const [showAcceptModal, setShowAcceptModal] = useState(false);
  const [acceptNotes, setAcceptNotes] = useState('');
  const [implementationDetails, setImplementationDetails] = useState('');
  
  // Reject modal state
  const [showRejectModal, setShowRejectModal] = useState(false);
  const [rejectionCategory, setRejectionCategory] = useState<SelectProps.Option | null>(null);
  const [rejectionReason, setRejectionReason] = useState('');
  const [detailedFeedback, setDetailedFeedback] = useState('');
  const [suggestedImprovement, setSuggestedImprovement] = useState('');
  
  // Result notification
  const [actionResult, setActionResult] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  const isChampion = userInfo?.groups.includes('finops-admin') || userInfo?.groups.includes('champion');
  const canAction = isChampion && finding?.status === 'pending';

  useEffect(() => {
    async function loadFinding() {
      if (!findingId) return;
      try {
        const data = await getFinding(findingId);
        setFinding(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load finding');
      } finally {
        setLoading(false);
      }
    }
    loadFinding();
  }, [findingId]);

  const handleAccept = async () => {
    if (!findingId) return;
    setActionLoading(true);
    try {
      const result = await acceptFinding(findingId, {
        notes: acceptNotes,
        implementationDetails,
        createLearning: true,
      });
      setActionResult({
        type: 'success',
        message: `Finding accepted. You earned ${result.pointsEarned} points and saved $${result.savingsUsd}/month.`,
      });
      setShowAcceptModal(false);
      // Reload finding to get updated status
      const updated = await getFinding(findingId);
      setFinding(updated);
    } catch (err) {
      setActionResult({
        type: 'error',
        message: err instanceof Error ? err.message : 'Failed to accept finding',
      });
    } finally {
      setActionLoading(false);
    }
  };

  const handleReject = async () => {
    if (!findingId || !rejectionCategory) return;
    setActionLoading(true);
    try {
      const result = await rejectFinding(findingId, {
        reason: rejectionReason,
        category: rejectionCategory.value || '',
        detailedFeedback,
        suggestedImprovement,
      });
      setActionResult({
        type: 'success',
        message: `Finding rejected. You earned ${result.pointsEarned} points for your feedback.${result.learningRecorded ? ' A learning has been recorded.' : ''}`,
      });
      setShowRejectModal(false);
      // Reload finding to get updated status
      const updated = await getFinding(findingId);
      setFinding(updated);
    } catch (err) {
      setActionResult({
        type: 'error',
        message: err instanceof Error ? err.message : 'Failed to reject finding',
      });
    } finally {
      setActionLoading(false);
    }
  };

  if (loading) {
    return (
      <Box textAlign="center" padding="xxl">
        <Spinner size="large" />
        <Box variant="p" margin={{ top: 's' }}>Loading finding details...</Box>
      </Box>
    );
  }

  if (error || !finding) {
    return (
      <Alert type="error" header="Error loading finding">
        {error || 'Finding not found'}
      </Alert>
    );
  }

  return (
    <SpaceBetween size="l">
      {actionResult && (
        <Alert
          type={actionResult.type}
          dismissible
          onDismiss={() => setActionResult(null)}
        >
          {actionResult.message}
        </Alert>
      )}

      <Header
        variant="h1"
        actions={
          canAction && (
            <SpaceBetween direction="horizontal" size="s">
              <Button onClick={() => setShowRejectModal(true)}>Reject</Button>
              <Button variant="primary" onClick={() => setShowAcceptModal(true)}>
                Accept
              </Button>
            </SpaceBetween>
          )
        }
      >
        <SpaceBetween direction="horizontal" size="s">
          <Badge color={PRIORITY_COLORS[finding.priority] as 'red' | 'grey' | 'blue' | 'green'}>
            {finding.priority.toUpperCase()}
          </Badge>
          <Badge color={STATUS_COLORS[finding.status] as 'blue' | 'green' | 'grey'}>
            {finding.status}
          </Badge>
          {finding.title}
        </SpaceBetween>
      </Header>

      <Tabs
        tabs={[
          {
            id: 'details',
            label: 'Details',
            content: (
              <SpaceBetween size="l">
                <Container header={<Header variant="h2">Overview</Header>}>
                  <ColumnLayout columns={2} variant="text-grid">
                    <Box>
                      <Box variant="awsui-key-label">Service</Box>
                      <Box>{finding.service}</Box>
                    </Box>
                    <Box>
                      <Box variant="awsui-key-label">Category</Box>
                      <Box>{finding.category}</Box>
                    </Box>
                    <Box>
                      <Box variant="awsui-key-label">Estimated Monthly Savings</Box>
                      <Box fontSize="heading-l" color="text-status-success">
                        ${finding.estimatedSavingsUsd.toLocaleString()}
                      </Box>
                    </Box>
                    <Box>
                      <Box variant="awsui-key-label">Created</Box>
                      <Box>{new Date(finding.createdAt).toLocaleString()}</Box>
                    </Box>
                  </ColumnLayout>
                </Container>

                <Container header={<Header variant="h2">Description</Header>}>
                  <Box>{finding.description}</Box>
                </Container>

                {(finding.accountIds?.length || finding.resourceIds?.length || finding.tags?.length) && (
                  <Container header={<Header variant="h2">Scope</Header>}>
                    <KeyValuePairs
                      columns={3}
                      items={[
                        ...(finding.accountIds?.length ? [{
                          label: 'Account IDs',
                          value: finding.accountIds.join(', '),
                        }] : []),
                        ...(finding.resourceIds?.length ? [{
                          label: 'Resource IDs',
                          value: finding.resourceIds.join(', '),
                        }] : []),
                        ...(finding.tags?.length ? [{
                          label: 'Tags',
                          value: finding.tags.join(', '),
                        }] : []),
                      ]}
                    />
                  </Container>
                )}
              </SpaceBetween>
            ),
          },
          {
            id: 'history',
            label: 'History',
            content: (
              <Container header={<Header variant="h2">Action History</Header>}>
                <SpaceBetween size="m">
                  {finding.status === 'pending' && (
                    <Box>
                      <StatusIndicator type="pending">Awaiting review</StatusIndicator>
                    </Box>
                  )}
                  
                  {finding.status === 'accepted' && (
                    <ExpandableSection header="Acceptance Details" defaultExpanded>
                      <KeyValuePairs
                        columns={2}
                        items={[
                          { label: 'Accepted By', value: finding.acceptedBy || '-' },
                          { label: 'Accepted At', value: finding.acceptedAt ? new Date(finding.acceptedAt).toLocaleString() : '-' },
                          { label: 'Notes', value: finding.acceptNotes || '-' },
                          { label: 'Implementation Details', value: finding.implementationDetails || '-' },
                        ]}
                      />
                    </ExpandableSection>
                  )}
                  
                  {finding.status === 'rejected' && (
                    <ExpandableSection header="Rejection Details" defaultExpanded>
                      <KeyValuePairs
                        columns={2}
                        items={[
                          { label: 'Rejected By', value: finding.rejectedBy || '-' },
                          { label: 'Rejected At', value: finding.rejectedAt ? new Date(finding.rejectedAt).toLocaleString() : '-' },
                          { label: 'Category', value: REJECTION_CATEGORIES.find(c => c.value === finding.rejectionCategory)?.label || finding.rejectionCategory || '-' },
                          { label: 'Reason', value: finding.rejectionReason || '-' },
                        ]}
                      />
                    </ExpandableSection>
                  )}
                </SpaceBetween>
              </Container>
            ),
          },
        ]}
      />

      <Box float="right">
        <Button onClick={() => navigate('/findings')}>Back to Backlog</Button>
      </Box>

      {/* Accept Modal */}
      <Modal
        visible={showAcceptModal}
        onDismiss={() => setShowAcceptModal(false)}
        header="Accept Finding"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setShowAcceptModal(false)}>Cancel</Button>
              <Button variant="primary" onClick={handleAccept} loading={actionLoading}>
                Accept Finding
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="info">
            Accepting this finding will award you points based on the savings amount (${finding.estimatedSavingsUsd.toLocaleString()}/month).
          </Alert>
          <FormField
            label="Notes"
            description="Add any notes about your decision (optional)"
          >
            <Textarea
              value={acceptNotes}
              onChange={({ detail }) => setAcceptNotes(detail.value)}
              placeholder="Why did you accept this recommendation?"
            />
          </FormField>
          <FormField
            label="Implementation Details"
            description="Describe how you plan to implement this (optional)"
          >
            <Textarea
              value={implementationDetails}
              onChange={({ detail }) => setImplementationDetails(detail.value)}
              placeholder="Steps to implement this cost saving..."
            />
          </FormField>
        </SpaceBetween>
      </Modal>

      {/* Reject Modal */}
      <Modal
        visible={showRejectModal}
        onDismiss={() => setShowRejectModal(false)}
        header="Reject Finding"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setShowRejectModal(false)}>Cancel</Button>
              <Button
                variant="primary"
                onClick={handleReject}
                loading={actionLoading}
                disabled={!rejectionCategory || !rejectionReason}
              >
                Reject Finding
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween size="m">
          <Alert type="info">
            Your feedback helps improve future recommendations. You'll earn points for providing quality feedback.
          </Alert>
          <FormField
            label="Rejection Category"
            constraintText="Required"
          >
            <Select
              selectedOption={rejectionCategory}
              onChange={({ detail }) => setRejectionCategory(detail.selectedOption)}
              options={REJECTION_CATEGORIES.map(c => ({ value: c.value, label: c.label }))}
              placeholder="Select a category"
            />
          </FormField>
          <FormField
            label="Reason"
            constraintText="Required"
          >
            <Input
              value={rejectionReason}
              onChange={({ detail }) => setRejectionReason(detail.value)}
              placeholder="Brief reason for rejection"
            />
          </FormField>
          <FormField
            label="Detailed Feedback"
            description="Help us understand why this recommendation doesn't apply (optional)"
          >
            <Textarea
              value={detailedFeedback}
              onChange={({ detail }) => setDetailedFeedback(detail.value)}
              placeholder="Provide additional context..."
            />
          </FormField>
          <FormField
            label="Suggested Improvement"
            description="How could this recommendation be improved? (optional)"
          >
            <Textarea
              value={suggestedImprovement}
              onChange={({ detail }) => setSuggestedImprovement(detail.value)}
              placeholder="What would make this recommendation more actionable?"
            />
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  );
}
