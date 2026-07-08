#!/usr/bin/env bash
# Run once on the production checkout to enable auto-rebuild-on-pull:
#   bash infra/hooks/install.sh
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cp "$repo_root/infra/hooks/post-merge" "$repo_root/.git/hooks/post-merge"
chmod +x "$repo_root/.git/hooks/post-merge"
git config deploy.autoRebuild true

echo "Installed: 'git pull' will now run 'docker compose up -d --build' in infra/."
echo "Disable any time with: git config deploy.autoRebuild false"
