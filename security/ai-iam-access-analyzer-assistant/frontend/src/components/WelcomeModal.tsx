import { useState, useEffect } from "react";
import Modal from "@cloudscape-design/components/modal";
import Box from "@cloudscape-design/components/box";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Button from "@cloudscape-design/components/button";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Icon from "@cloudscape-design/components/icon";

const STORAGE_KEY = "iam-analyzer-welcome-dismissed";

export default function WelcomeModal() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const dismissed = localStorage.getItem(STORAGE_KEY);
    if (!dismissed) {
      setVisible(true);
    }
  }, []);

  const handleDismiss = () => {
    localStorage.setItem(STORAGE_KEY, "true");
    setVisible(false);
  };

  return (
    <Modal
      visible={visible}
      onDismiss={handleDismiss}
      header="Welcome to IAM Security Assistant"
      size="large"
      footer={
        <Box float="right">
          <Button variant="primary" onClick={handleDismiss}>
            Get Started
          </Button>
        </Box>
      }
    >
      <SpaceBetween size="l">
        <Box variant="p">
          A conversational assistant that helps you understand and improve your
          AWS IAM security posture using natural language.
        </Box>

        <ColumnLayout columns={3} variant="text-grid">
          <div>
            <Box variant="h4">
              <Icon name="search" /> Analyze
            </Box>
            <Box variant="p" color="text-body-secondary">
              Query IAM Access Analyzer findings, review unused permissions,
              and understand your security posture.
            </Box>
          </div>
          <div>
            <Box variant="h4">
              <Icon name="edit" /> Generate
            </Box>
            <Box variant="p" color="text-body-secondary">
              Create least-privilege policies from CloudTrail data, build
              policies for new workloads, and export as JSON or CDK.
            </Box>
          </div>
          <div>
            <Box variant="h4">
              <Icon name="status-warning" /> Protect
            </Box>
            <Box variant="p" color="text-body-secondary">
              Blast radius analysis before changes, dependency mapping,
              policy validation, and change request generation.
            </Box>
          </div>
        </ColumnLayout>

        <Box variant="h4">Quick Tips</Box>
        <SpaceBetween size="xs">
          <Box variant="p">
            <strong>Guided Mode</strong> (default) explains everything in
            detail with AWS documentation links — great for learning.
          </Box>
          <Box variant="p">
            <strong>Quick Mode</strong> gives concise, data-focused answers
            for experienced IAM practitioners.
          </Box>
          <Box variant="p">
            Click the <strong>suggested prompts</strong> below the chat to get
            started quickly, or ask anything in your own words — the
            suggestions are just starting points, not limits. You can ask
            about specific roles, policies, or IAM concepts freely.
          </Box>
        </SpaceBetween>

        <div
          style={{
            padding: "12px 16px",
            borderRadius: "8px",
            backgroundColor: "var(--color-background-status-info)",
            border: "1px solid var(--color-border-status-info)",
          }}
        >
          <SpaceBetween size="xs">
            <Box variant="h4">Production Use</Box>
            <Box variant="p" fontSize="body-s">
              This deployment uses a standalone Cognito User Pool with demo
              credentials. To use with your corporate identity:
            </Box>
            <Box variant="p" fontSize="body-s">
              <strong>1.</strong> Edit{" "}
              <code>infrastructure/cdk/stacks/auth_construct.py</code>
            </Box>
            <Box variant="p" fontSize="body-s">
              <strong>2.</strong> Add a SAML or OIDC identity provider
              (Okta, Azure AD, Ping, Google Workspace, or AWS IAM Identity
              Center)
            </Box>
            <Box variant="p" fontSize="body-s">
              <strong>3.</strong> Disable self-signup and enable MFA
            </Box>
            <Box variant="p" fontSize="body-s">
              <strong>4.</strong> Redeploy:{" "}
              <code>./deploy-all.sh</code>
            </Box>
            <Box variant="p" fontSize="body-s" color="text-body-secondary">
              Full code examples for SAML, OIDC, and IAM Identity Center are
              in the README under "Identity & Authentication (Production
              Use)".
            </Box>
          </SpaceBetween>
        </div>
      </SpaceBetween>
    </Modal>
  );
}
