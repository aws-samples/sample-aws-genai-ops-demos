# Mutating actions — write-back opt-in

DART is **read-only on customer data by default**. It analyzes a dataset and
produces a report; it never modifies or deletes the dataset you point it at.
The only thing the task writes by default is its own report to the demo's
output bucket (`s3://dart-agent-output-<account>-<region>/reports/<run_id>.md`)
and a best-effort audit record to the demo's DynamoDB table — both resources the
stack owns.

This document covers the one **opt-in** write-back capability: writing a
*cleaned* or *PII-redacted* copy of a dataset back to a bucket you own. It is
disabled by default and must be enabled deliberately.

> This is the **write** opt-in. Letting the task **read** a dataset that lives
> in one of your own source buckets is a separate opt-in documented in the
> README section "Scanning a dataset in your own S3 bucket" (CDK
> `--context inputBuckets=...` / Terraform `var.input_buckets`, matched by
> `ALLOWED_S3_BUCKETS`). Read access does not grant write access.

## Why write actions are disabled by default

- `apply_safe_fixes` and `scan_pii` **never modify the original input**. They
  always write their output to a **new** file path (auto-generated when you do
  not supply `output_path`), so a fix run cannot corrupt or destroy the dataset
  you asked it to audit.
- The default task role has `s3:PutObject` **only on the demo's own output
  bucket**. It has no write permission to any customer/source bucket, so a
  cleaned copy cannot be written outside the demo's own bucket unless you grant
  it.
- Writing a redacted copy of a dataset is a data-handling decision with
  compliance implications (where the redacted data lands, who can read it, how
  long it is retained). That decision belongs to the operator, not to a demo
  default.

So by default a cleaned/redacted copy lands under
`s3://dart-agent-output-<account>-<region>/...`, which the task role already
writes. You only need the opt-in below if you want the cleaned copy written to a
**different** bucket you own.

## Enabling write-back to your own bucket

To let the task write a cleaned/redacted copy to a bucket you control, attach a
**scoped inline policy** to the task role granting `s3:PutObject` on that
specific bucket and prefix — never `*`. Replace the placeholders:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DartWriteBackCleanedCopy",
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:<PARTITION>:s3:::<CLEANED_OUTPUT_BUCKET>/<CLEANED_PREFIX>/*"
    }
  ]
}
```

If the destination bucket is encrypted with a customer-managed KMS key, also
grant `kms:GenerateDataKey` and `kms:Encrypt` on that key's ARN (scoped to the
key, never `*`):

```json
{
  "Sid": "DartWriteBackKms",
  "Effect": "Allow",
  "Action": ["kms:GenerateDataKey", "kms:Encrypt"],
  "Resource": "arn:<PARTITION>:kms:<REGION>:<ACCOUNT_ID>:key/<KEY_ID>"
}
```

Then pass the destination as the tool's `output_path` (e.g.
`s3://<CLEANED_OUTPUT_BUCKET>/<CLEANED_PREFIX>/train.cleaned.jsonl`) and add that
bucket to `ALLOWED_S3_BUCKETS` so path validation permits it.

## Review checklist before enabling write-back

- [ ] The destination bucket and prefix are **owned by you** and intended to
      hold redacted/cleaned data.
- [ ] The `s3:PutObject` resource is scoped to the **specific** bucket and
      prefix — not `*`, not the whole bucket.
- [ ] If the destination is KMS-encrypted, the `kms` grant is scoped to that
      **one key ARN**.
- [ ] The destination bucket's own access controls (bucket policy, Block Public
      Access, encryption) are appropriate for redacted data.
- [ ] A retention/lifecycle policy exists for the cleaned copies if required by
      your compliance posture.
- [ ] `ALLOWED_S3_BUCKETS` lists the destination bucket so path validation and
      the IAM grant agree.
- [ ] PII redaction output has been reviewed: redaction is best-effort over the
      entities Amazon Comprehend detects and is **not** a guarantee that every
      sensitive value was removed.
