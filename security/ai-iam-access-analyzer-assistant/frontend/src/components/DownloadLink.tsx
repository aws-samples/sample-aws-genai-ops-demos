// DownloadLink intercepts clicks on API Gateway /downloads/ URLs and turns
// them into an authenticated fetch + Blob download in the browser.
//
// Why this exists: the assistant's `[Download here](url)` markdown links
// point at a Cognito-authed API GW route (`GET /downloads/{proxy+}`, see
// `src/download.py`). Browsers can't attach Authorization headers to plain
// navigation, so a direct click on the anchor would land at API GW without
// a Bearer token and get 401'd. Interception via fetch + Blob download is
// the standard authenticated-download pattern for SPA + API GW auth models.
//
// This replaced the earlier S3 presigned-URL flow, which broke on boto3
// 1.42.97's role-chained STS-signed URLs (S3 rejected the URLs with
// `InvalidToken` even though the underlying creds were valid — verified via
// head_object probes).
import { useState } from "react";
import { fetchAuthSession } from "aws-amplify/auth";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import StatusIndicator from "@cloudscape-design/components/status-indicator";

const API_ENDPOINT = import.meta.env.VITE_API_ENDPOINT as string | undefined;

/**
 * True iff the given href points at our Cognito-authed /downloads/ route.
 * Matches strictly against the frontend's baked API_ENDPOINT so we don't
 * accidentally intercept unrelated external links that happen to have
 * `/downloads/` somewhere in the path.
 */
export function isDownloadUrl(href: string | undefined): boolean {
  if (!href || !API_ENDPOINT) {
    return false;
  }
  return href.startsWith(API_ENDPOINT + "downloads/");
}

interface DownloadLinkProps {
  href: string;
  children: React.ReactNode;
}

/**
 * A link-styled button that, when clicked, authenticates against the
 * Cognito session, fetches the download endpoint, decodes the base64
 * envelope, and triggers a browser download with the correct filename.
 * Shows loading and error states inline.
 */
// How long to show the "Downloaded ✓" confirmation after a successful
// download before reverting to the idle label. 2 seconds is long enough
// to notice, short enough not to be nagging.
const SUCCESS_CONFIRMATION_MS = 2000;

export function DownloadLink({ href, children }: DownloadLinkProps) {
  const [status, setStatus] = useState<
    "idle" | "downloading" | "success" | "error"
  >("idle");
  const [errorMessage, setErrorMessage] = useState<string>("");

  const handleClick = async () => {
    setStatus("downloading");
    setErrorMessage("");
    try {
      await downloadFromApi(href);
      // Explicit visual confirmation. In some browser+OS combinations
      // (and in headless / automation profiles like Amazon Quick's
      // sandbox) the OS-level download notification is silent or lands
      // out of sight, making the button click look like a no-op even
      // when the file downloaded correctly. This removes the ambiguity.
      setStatus("success");
      setTimeout(() => {
        setStatus((current) => (current === "success" ? "idle" : current));
      }, SUCCESS_CONFIRMATION_MS);
    } catch (err) {
      setStatus("error");
      setErrorMessage(
        err instanceof Error && err.message
          ? err.message
          : "Download failed."
      );
    }
  };

  return (
    <>
      <Button
        variant="inline-link"
        onClick={handleClick}
        loading={status === "downloading"}
        loadingText="Downloading…"
        disabled={status === "downloading"}
      >
        {children}
      </Button>
      {status === "success" && (
        <Box padding={{ top: "xxs" }}>
          <StatusIndicator type="success">
            Downloaded — check your browser's downloads.
          </StatusIndicator>
        </Box>
      )}
      {status === "error" && (
        <Box padding={{ top: "xxs" }}>
          <StatusIndicator type="error">{errorMessage}</StatusIndicator>
        </Box>
      )}
    </>
  );
}

/**
 * Fetch the download endpoint with the current Cognito session, decode the
 * base64 payload, and trigger a browser download.
 *
 * Response envelope from `src/download.py`:
 *   { filename, content_type, size_bytes, content_b64, s3_key }
 */
async function downloadFromApi(url: string): Promise<void> {
  const session = await fetchAuthSession();
  const token = session.tokens?.idToken?.toString();
  if (!token) {
    throw new Error("Not signed in. Refresh the page and sign in again.");
  }

  const response = await fetch(url, {
    method: "GET",
    headers: { Authorization: token },
  });

  if (!response.ok) {
    // 404: file not found or path-validation failure (uniform response).
    if (response.status === 404) {
      throw new Error(
        "File not found. It may have expired from S3 (90-day lifecycle) — try 'list my exports' to see what's still available."
      );
    }
    // 413: over the inline-download size cap. The backend body includes
    // a concrete AWS CLI fallback command in `error`.
    if (response.status === 413) {
      const body = await response.json().catch(() => ({}));
      throw new Error(
        body.error ||
          "File too large for inline download. Use `aws s3 cp` to retrieve it."
      );
    }
    // 401 / 403: auth failed. Session likely expired.
    if (response.status === 401 || response.status === 403) {
      throw new Error("Not authorized. Sign out and back in, then retry.");
    }
    // Anything else: surface the status.
    const body = await response.json().catch(() => ({}));
    throw new Error(
      body.error || body.message || `Download failed (${response.status}).`
    );
  }

  const payload = await response.json();
  const { filename, content_type, content_b64 } = payload as {
    filename?: string;
    content_type?: string;
    content_b64?: string;
  };

  if (!filename || !content_b64) {
    throw new Error("Download endpoint returned an unexpected response.");
  }

  const blob = base64ToBlob(content_b64, content_type || "application/octet-stream");
  triggerBrowserDownload(blob, filename);
}

/**
 * Decode a base64 string to a Blob with the given MIME type. Uses a
 * chunked Uint8Array conversion so large payloads don't blow the JS
 * engine's call-stack argument limit that a naive
 * `Uint8Array.from(binaryString, c => c.charCodeAt(0))` can trigger.
 */
function base64ToBlob(b64: string, contentType: string): Blob {
  const binaryString = atob(b64);
  const bytes = new Uint8Array(binaryString.length);
  for (let i = 0; i < binaryString.length; i++) {
    bytes[i] = binaryString.charCodeAt(i);
  }
  return new Blob([bytes], { type: contentType });
}

/**
 * Trigger a browser download with the given filename. Creates and
 * revokes a temporary object URL so we don't leak memory across many
 * downloads in a long-lived session.
 */
function triggerBrowserDownload(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = filename;
    // Some browsers require the anchor to be in the DOM before .click().
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}
