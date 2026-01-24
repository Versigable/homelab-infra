# GitLab CI template (deploy job)

```yaml
deploy-traefik:
  stage: deploy
  tags: ["traefik"]
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
      changes:
        - hosts/traefik/**/*
  script:
    - whoami
    - pwd
    - ls -la
    - ls -la hosts/traefik/secrets
    - bash hosts/traefik/scripts/deploy.sh
```

Notes:
- `tags` ensures the job runs on the correct host runner
- `changes` prevents unnecessary deploys
- The deploy script handles SOPS decrypt + compose up
