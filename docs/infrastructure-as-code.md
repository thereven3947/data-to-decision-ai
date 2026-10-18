# Infrastructure as code

The Mac mini runs D2D (see [deployment.md](deployment.md)); two optional cloud pieces sit around
it. All of it is code, reviewed in pull requests and checked in CI (`infra` job, or `make infra`),
without cloud credentials.

| Path | Tool | What it manages |
|---|---|---|
| [`infra/ansible`](../infra/ansible/) | Ansible | The Mac mini host: power settings, `uv`, `gh`, Ollama as a LaunchAgent, models, `.env` permissions |
| [`infra/terraform/gcp`](../infra/terraform/gcp/) | Terraform | BigQuery dataset for the [analytics warehouse](analytics-warehouse.md), least-privilege loader, dashboard viewers |
| [`infra/terraform/aws`](../infra/terraform/aws/) | Terraform | Off-site S3 copy of the nightly Postgres backups, write-only uploader |

## Ansible: the Mac mini host

[`site.yml`](../infra/ansible/site.yml) turns the manual runbook ([mac-mini-setup.md](mac-mini-setup.md)
steps 1–3 and 6) into idempotent tasks: a second run changes nothing. It uses only
`ansible.builtin` modules and installs everything per user under `~/.local`, like the runbook.

```bash
uvx --from ansible-core ansible-playbook -i infra/ansible/inventory.example.ini infra/ansible/site.yml
uvx --from ansible-core ansible-playbook ... --tags power --ask-become-pass   # pmset needs sudo
uvx --from ansible-core ansible-playbook ... --tags models                    # pull missing models
```

GUI installs (OrbStack, Tailscale) and the self-hosted runner (`deploy/runner/install.sh`) stay
manual: they need an interactive login or a GitHub admin token.

## Terraform: BigQuery warehouse (GCP)

```bash
cd infra/terraform/gcp
cp terraform.tfvars.example terraform.tfvars   # project_id, dashboard_viewers
terraform init && terraform plan && terraform apply
gcloud config set auth/impersonate_service_account "$(terraform output -raw loader_service_account)"
analytics/bigquery/load.sh "$PROJECT_ID" d2d_analytics data/warehouse
```

- The loader can edit **this dataset only** (`bigquery.dataEditor` on the dataset) and run jobs
  (`bigquery.jobUser` on the project). Viewers get `bigquery.dataViewer`.
- No service-account key is created: a key in Terraform state is a secret at rest. Use your own
  login and impersonate the loader.
- `delete_contents_on_destroy = false`, so `terraform destroy` cannot silently drop loaded tables.

## Terraform: off-site backups (AWS)

`deploy/backup.sh` keeps 14 nightly dumps on the Mac mini. A disk failure or a stolen machine would
lose them all, so the AWS stack adds an off-site copy:

- **Bucket:** private (all public access blocked, owner-enforced), versioned, encrypted (SSE-S3),
  TLS-only by bucket policy, tiered to Glacier Instant Retrieval after 30 days and expired after
  365 (noncurrent versions 30 days later).
- **Uploader:** an IAM user that may only `s3:PutObject` under `production/` and `staging/`. It
  cannot read, list or delete, so a compromised Mac mini cannot erase the off-site history.

```bash
cd infra/terraform/aws
cp terraform.tfvars.example terraform.tfvars   # bucket_name
terraform init && terraform apply
aws iam create-access-key --user-name d2d-backup-uploader   # store in ~/.aws/credentials as [d2d-backup]
echo "D2D_BACKUP_S3_URI=$(terraform output -raw backup_uri)" >> ~/d2d/production/.env
```

`backup.sh` uploads each new dump when `D2D_BACKUP_S3_URI` is set (environment or the environment's
`.env`) and the `aws` CLI is installed. Without it, backups behave exactly as before. A failed
upload is logged and the local dump is kept.

## Tests without a cloud account

`terraform test` runs the files in each `tests/` folder against **mocked providers**:

- AWS: versioning, public-access block, encryption, the TLS-only deny statement, the uploader's
  `PutObject`-only policy and exact prefixes, and the variable validations.
- GCP: dataset name, the loader's two roles, read-only viewers, no delete-on-destroy, and the
  dataset-name validation.

CI also runs `terraform fmt -check`, `terraform validate` and `ansible-playbook --syntax-check`.
Provider versions are pinned in `versions.tf` and locked in `.terraform.lock.hcl` (hashes for all
platforms).
