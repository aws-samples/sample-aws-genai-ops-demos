# lambda-migration-demo-fixture

Source of three AWS Lambda functions on deprecated runtimes, used by the AI Lambda Runtime Migration Assistant demo to try AWS Transform on real code.

| Folder | Function | Runtime today | Target runtime | Tests |
|---|---|---|---|---|
| `nodejs/` | `lambda-migration-mock-nodejs` (order token service) | nodejs16.x | nodejs24.x | `npm test` |
| `python/` | `lambda-migration-mock-python` (partner webhook receiver) | python3.9 | python3.13 | `python -m pytest` |
| `dotnet/` | `lambda-migration-mock-dotnet` (partner session gateway) | dotnet6 | dotnet10 | `dotnet test` |

Run the test commands from the repository root. Each folder also holds the function's AWS SAM `template.yaml`, so a migration updates the code and the infrastructure definition together.

## Why these functions can't just switch runtime

Each function is written the way it typically was for its runtime. Everything works on today's runtime, and the tests pass there. But each one relies on APIs that were later removed from the language or that the newer Lambda runtime no longer supports. Changing `Runtime:` alone breaks the function: the tests fail on the target runtime, and so would the deployed function.

A migration therefore has to find every one of these patterns, replace it with the supported equivalent, upgrade the dependencies and the tests, and only then bump the runtime. That is the work we expect AWS Transform to do. It must leave the two other folders untouched.

## Node.js 16 → 24: order token service

`nodejs/index.js` decrypts an order token sent by a partner, validates the order and returns a summary. The token secret comes from SSM Parameter Store.

| Code we ship | Why it breaks on nodejs24.x | What we expect AWS Transform to do |
|---|---|---|
| `crypto.createCipher('aes192', secret)` and `crypto.createDecipher` | Removed in Node.js 22 (DEP0106): they derived the key with MD5 and no salt, and used a static IV. The calls throw `TypeError`. | Switch to `crypto.createCipheriv` / `createDecipheriv` with a key derived by `scrypt` or `pbkdf2` and a random IV stored with the token. |
| `util.isString()`, `util.isNumber()`, `util.isDate()` to validate the order, `util.log()` to log | Removed in Node.js 23. | Use `typeof` / `instanceof` checks and `console.log`. |
| `require('aws-sdk')` (AWS SDK for JavaScript v2), with `aws-sdk` only in `devDependencies` | The nodejs16.x runtime bundled SDK v2. Since nodejs18.x the runtime bundles SDK v3 instead, so the module is missing in the deployed function. | Move to `@aws-sdk/client-ssm` (`SSMClient`, `GetParameterCommand`) and declare it as a production dependency. |
| `exports.handler = (event, context, callback) => …` | The nodejs24.x runtime no longer supports callback-based handlers. | Rewrite the handler and `getSecret` as `async` functions that return their result. |

The last two rows are rules of the Lambda runtime, not of Node.js itself. `test/lambda-runtime.js` models them from the Node.js version the tests run on, so `npm test` fails the way the deployed function would.

## Python 3.9 → 3.13: partner webhook receiver

`python/lambda_function.py` accepts a JSON webhook, checks the content type and the partner's client version, runs an optional enrichment plugin (`enrich_plugin.py`) and returns the accepted event. Its settings are in `settings.ini`.

| Code we ship | Why it breaks on python3.13 | What we expect AWS Transform to do |
|---|---|---|
| `from distutils.version import LooseVersion` to compare client versions | `distutils` was removed in Python 3.12 (PEP 632). The import fails. | Compare versions with `packaging.version` or an equivalent numeric comparison. `2.10.1` must still be newer than `2.4.0`. |
| `import imp` and `imp.load_source()` to load the plugin | `imp` was removed in Python 3.12. | Load the plugin with `importlib.util`. |
| `configparser.SafeConfigParser()` to read `settings.ini` | Removed in Python 3.12 (it was an alias of `ConfigParser`). | Use `configparser.ConfigParser`. |
| `import cgi` and `cgi.parse_header()` to read `Content-Type` | `cgi` was removed in Python 3.13 (PEP 594). | Parse the header with `email.message.Message`. |
| `self.assertEquals(...)` in `test_handler.py` | The unittest aliases were removed in Python 3.12. | Use `assertEqual`: the tests need upgrading too, not only the code. |

## .NET 6 → 10: partner session gateway

`dotnet/src/MockDotnet/Function.cs` checks a partner's client certificate, derives the partner's API key from a password and caches the session for the next call. The project sets `TreatWarningsAsErrors`, a common team policy, so obsolete APIs fail the build.

| Code we ship | Why it breaks on dotnet10 | What we expect AWS Transform to do |
|---|---|---|
| `BinaryFormatter` to serialize the cached session (its warning, SYSLIB0011, is suppressed in the project) | Since .NET 9 every `BinaryFormatter` call throws `PlatformNotSupportedException`. | Serialize the session with `System.Text.Json` and drop the suppression and the `[Serializable]` attribute. |
| `new X509Certificate2(byte[])` to read the certificate | Obsolete since .NET 9 (SYSLIB0057), so a build error with warnings-as-errors. | Load it with `X509CertificateLoader.LoadCertificate`. |
| `new Rfc2898DeriveBytes(password, salt)`: implicit SHA-1, 1,000 iterations | Obsolete since .NET 7 (SYSLIB0041), so a build error with warnings-as-errors. | Use `Rfc2898DeriveBytes.Pbkdf2` with an explicit hash algorithm (SHA-256) and iteration count. |
| `net6.0` target framework, `Amazon.Lambda.Core` 2.1.0, `Amazon.Lambda.Serialization.SystemTextJson` 2.3.0, xUnit 2.4 | .NET 6-era project and packages. | Target `net10.0` in both projects and upgrade the Lambda and test packages. |

## What to look at in a migration branch

Each migration pushes a branch `lambda-migration/<target>-<job>`; `main` is never changed. Compare it with `main`, and check:

- every row of the function's table above is addressed, and the tests pass on the target runtime;
- `Runtime:` is updated in the function's `template.yaml`;
- the two other folders are untouched;
- the behavior changes a real team would have to plan for. A new cipher or key derivation makes tokens, sessions and API keys issued before the migration unreadable, for example.

AWS Transform is an AI agent: two runs can produce different code, so review the branch like any pull request.
