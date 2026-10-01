# P6 device key proof record, 2026-10-01

Closes the P6 acceptance: one real ask answered in the browser extension popup with the owner's device id on the audit row. Run by Al (approvals, popup answer) and HCA (execution through the Mac bridge). Project id, region and service URL are runbook values held by the owner, not recorded here.

## What is live

| Item | Value |
| --- | --- |
| Source commit | 754046d3178e035e011419b266f522e9bb365359 (main, after PR 20) |
| Build | 7476aeed-15cb-4e31-9aa7-a43005ed53dc, SUCCESS, 2m39s, image tag 754046d3178e035e011419b266f522e9bb365359 |
| Service revision | hca-api-service-00004-fl4 at 100 percent |
| Previous revision | hca-api-service-00003-cxv (P6 main 826e326e, PR 19), replaced |
| Registered device | dev_a90693e0d5e15c341aefe6ce38f6275f, label "Al MacBook Chrome" |

## Proof (read with the agent key of caller agent-p6-proof)

Request 0b5b9c8f9c204606aa2450a070eed64d, title "P6 proof, take 3: which letter?", control radio, options a and b, suggested a.

- state: answered
- value: a
- answered_by: Al MacBook Chrome
- answered_at: 2026-10-01T17:44:33Z
- surface: browser_overlay
- device: dev_a90693e0d5e15c341aefe6ce38f6275f

The answer POST was signed by the extension's P-256 key and verified against the registered device; the audit row records that device id.

## Defect found and fixed during the run (PR 20)

Take 1 (5ffb36ffa5d343fbbf9fbd01fd3ca6a0) expired while gcloud on the Mac hung on IPv6. Take 2 (fb7de27830514cb78b9b5fc7f37c8a2f) surfaced the defect: the popup answer POST returned 404 request not found because GET /v1/inbox items carried no request_id, so the extension fell back to item.id (undefined) and POSTed /v1/requests/undefined/answer. The inbox test keyed on titles and never caught it.

Fix, PR 20 squashed as 754046d3: each inbox item is the CollectionRequest fields plus request_id (the shape the create receipt and SSE event already used), the inbox test asserts request_id on every item, openapi.yaml documents it. No extension change was needed.

## Environment note

gcloud on the Mac hung when the USB LAN adapter had a broken IPv6 path. Fix applied by Al in Terminal: networksetup -setv6off on that adapter, then an interface down and up. No HCA code involved.

## Deploy path

There is no Cloud Build push trigger on main. Deploys run from the Mac with the runbook block: fresh shallow clone of main, gcloud builds submit with cloudbuild.yaml and the Cloud SQL connection name substitution. Al approves each deploy.
