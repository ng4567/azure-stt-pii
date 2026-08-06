# Azure Container Apps Deployment Plan

**Status:** Validated

## 1. Scope

Deploy the existing benchmark application to Azure Container Apps and preserve deployment details in `.azure/aca-deployment.local.json`, excluded through the repository-local Git exclude file so it cannot be committed accidentally.

**Mode:** MODERNIZE

**Profile:** Cost-optimized POC / engineering benchmark, small scale, single region, no special compliance requirement supplied.

## 2. Current Application

| Component | Technology | Deployment |
| --- | --- | --- |
| `backend` | Python 3.14, FastAPI, Uvicorn, ffmpeg, Azure SDKs | Internal-ingress Azure Container App on port 8000 |
| `frontend` | Bun TypeScript server and SPA; proxies `/api/*` | Public-ingress Azure Container App on port 3000 |

The backend already exposes `/api/health`, uses asynchronous/pollable jobs, and authenticates to Azure AI with `DefaultAzureCredential`. The ACA system-assigned managed identity will be selected automatically in Azure. No keys, CLI profile, or credentials will be copied into the image.

## 3. Target Architecture

| Resource | Planned name | Notes |
| --- | --- | --- |
| Resource group | `rg-stt-pii-aca` | New, East US |
| Azure Container Registry | `acrsttpiifd9180` | New Basic registry; globally available name confirmed |
| Container Apps environment | `cae-stt-pii-poc` | New, consumption workload profile |
| Backend app | `ca-stt-pii-api` | Internal ingress; min 1/max 1 replica to preserve in-memory jobs |
| Frontend app | `ca-stt-pii-web` | External ingress; min 0/max 1 replica |
| Speech/Voice Live | `<speech-resource>` | Existing resource in `<resource-group>`; unchanged |
| Azure Language + DeepSeek | `finance-app-resource` | Existing resource in `finance-app-ng`; unchanged |

The backend identity receives `Cognitive Services User` at the two existing AI-resource scopes and `AcrPull` on the registry. The frontend identity receives only `AcrPull`. The frontend reaches the backend over the Container Apps environment's internal FQDN.

POC caveat: uploads and in-memory job state are local to the single backend replica and can be lost on revision replacement. The checked-in benchmark remains available. Durable storage/job state is deferred rather than silently implying production durability.

## 4. Azure Context

**Subscription:** `ME-MngEnvMCAP461858-nikhilgopal-1` (`fd918039-a89e-49a7-8e32-af614b3765f9`)

**Location:** East US, selected to colocate ACA with the existing Speech/Voice Live resource. The Language/DeepSeek resource remains in East US 2.

**Policy constraints:** No subscription policy assignments were returned by the Azure CLI scan.

## 5. Deployment Recipe

**Recipe:** Azure CLI with generated Docker and deployment artifacts.

**Rationale:** The repository already has an imperative Azure deployment script and partial user-authored Foundry Bicep. The ACA deployment will not overwrite or redeploy that untracked Foundry infrastructure. Azure CLI provides the narrowest path for adding ACA, ACR, identities, role assignments, image builds, and verification while preserving existing AI resources.

## 6. Execution Plan

1. Add a production Bun frontend Dockerfile.
2. Add idempotent ACA deployment configuration/scripts without changing the existing AI resources.
3. Add `.azure/aca-deployment.local.json` to `.git/info/exclude`.
4. Validate Dockerfiles, Azure context, names, provider registration, role definitions, and deployment commands.
5. Build backend and frontend images with ACR Tasks.
6. Provision the Container Apps environment and apps.
7. Assign least-privilege RBAC and wait for role propagation.
8. Verify backend health through the frontend proxy and load the public UI.
9. Write subscription, resource group, app names, image tags, FQDNs, resource IDs, deployment timestamp, and verification results to the local ignored details file.

No existing resources will be deleted or replaced. Existing `infra/` remains untouched.

## 7. Validation Proof

Validated on 2026-08-06 against subscription `fd918039-a89e-49a7-8e32-af614b3765f9`.

- `bash -n .azure/deploy-aca.sh` - PASS.
- `git check-ignore --no-index .azure/aca-deployment.local.json` - PASS; local details are excluded by `.git/info/exclude`.
- `docker build -f backend/Dockerfile .` - PASS, image `sha256:6687524dcfb73bc2c1b1e59ab38e105d3f3fde54b807e4ecead5b3122fcbe25f`.
- `docker build -f frontend/Dockerfile .` - PASS, image `sha256:5f30b74f06c3c3102c2ff09327e27b4c0b5407e39585b44759b2290018705c7e`.
- Frontend container runtime request to `/` on port 3000 - PASS.
- Azure CLI authentication and target subscription - PASS.
- `Microsoft.App` and `Microsoft.ContainerRegistry` provider registration - PASS.
- ACR global-name availability for `acrsttpiifd9180` - PASS.
- Existing Speech and Foundry resource-scope resolution - PASS.
- Subscription policy assignments - none returned.
- Signed-in principal deployment authorization - PASS (`Owner` at subscription scope; role assignment capability available).
- Static secret scan - PASS; no embedded keys, passwords, or client secrets.
- Static role verification - PASS:
  - Backend identity: `AcrPull` scoped to the new ACR.
  - Frontend identity: `AcrPull` scoped to the new ACR.
  - Backend identity: `Cognitive Services User` scoped separately to the existing Speech and Foundry resources.
  - No generic subscription/resource-group data-plane role is assigned to either app.

## 7a. Functional Verification

- Backend production image: built successfully from `backend/Dockerfile`.
- Frontend production image: built successfully from `frontend/Dockerfile`.
- Frontend runtime: container started as configured and served `/` successfully on port 3000.
- Deployment script: `bash -n` passed; Azure CLI commands and authenticated subscription context are available.

## 8. Deployment Results

Deployed successfully on 2026-08-06 to Azure Container Apps in East US.

| Component | Result |
| --- | --- |
| Public frontend | `https://ca-stt-pii-web.wittysand-9e0f9316.eastus.azurecontainerapps.io` |
| Proxied health endpoint | `https://ca-stt-pii-web.wittysand-9e0f9316.eastus.azurecontainerapps.io/api/health` returned HTTP 200 with `{"status":"ok"}` |
| Cached benchmark endpoint | `https://ca-stt-pii-web.wittysand-9e0f9316.eastus.azurecontainerapps.io/api/benchmark/default` returned HTTP 200 with the built-in benchmark payload |
| Backend revision | `ca-stt-pii-api--0000003`, healthy, 100% traffic |
| Frontend revision | `ca-stt-pii-web--0000004`, healthy, 100% traffic |
| Backend image | `acrsttpiifd9180.azurecr.io/azure-stt-pii-backend:release-f35a6f69b6c1-20260806195222` |
| Frontend image | `acrsttpiifd9180.azurecr.io/azure-stt-pii-frontend:release-f35a6f69b6c1-20260806195222` |

Live role verification passed for both `AcrPull` assignments and both backend `Cognitive Services User` assignments. The frontend proxy removes the incoming `Host` header before forwarding requests so ACA can route them to the backend's internal FQDN.

Commit `f35a6f69b6c1` and its current worktree were deployed and verified at `2026-08-06T19:59:20Z`. Backend tests passed (`58 passed`), frontend type checking passed, and frontend tests passed (`19 passed`).

## 9. Generated Artifacts

- `frontend/Dockerfile` - production Bun frontend image running as the non-root `bun` user.
- `.azure/deploy-aca.sh` - idempotent, keyless ACA deployment and local-details writer.
- `.git/info/exclude` entry for `.azure/aca-deployment.local.json`.
