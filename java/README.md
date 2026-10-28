# java/: the change-management worker

A Spring Boot application using the Temporal Java SDK's Spring Boot starter. It implements `ChangeRequestWorkflow`, which the Python `IncidentWorkflow` starts as a child workflow, by name, on the `change-management` task queue. The two codebases share no code, only a JSON contract.

```bash
make java               # reads .env (Temporal address, namespace, TLS) and picks the right profile
mvn spring-boot:run     # or directly, with TEMPORAL_* variables exported in your shell
```

Requires Java 17+ and Maven 3.9+. The worker also serves a small HTTP API on 127.0.0.1:8081:

- `GET /health`
- `GET /services`: what the stand-in CMDB allows

## Where things are

| Path | What's there |
|---|---|
| `workflow/ChangeRequestWorkflowImpl.java` | Validate, open a change record, execute, close |
| `activities/ChangeActivitiesImpl.java` | The activities, as a Spring bean. Validation throws a non-retryable `ApplicationFailure` for services that aren't change-managed (the unrecoverable scenario). Execution heartbeats its progress, so a retry resumes mid-way |
| `cmdb/ServiceRegistry.java` | The stand-in CMDB. `legacy-billing` is deliberately missing |
| `model/ChangeRequest.java`, `model/ChangeResult.java` | The cross-language contract: snake_case JSON via `@JsonNaming` |
| `src/main/resources/application.yml` | Temporal connection, task queue, auto-discovery |
| `application-apikey.yml`, `application-mtls.yml`, `config/TemporalTlsConfig.java` | Authentication, chosen by `make java` from `.env`: API key, mTLS, or a private CA for self-hosted Temporal |

## The contract with Python

| | Value |
|---|---|
| Workflow type | `ChangeRequestWorkflow` (the interface's simple name) |
| Task queue | `change-management` |
| Input | `ChangeRequest` JSON: `incident_id`, `service`, `action`, `target_version`, `rationale`, `requested_by`, `approved_by` |
| Output | `ChangeResult` JSON: `change_id`, `status`, `steps`, `message` |

The Temporal SDK version is set by `temporal.version` in `pom.xml`.
