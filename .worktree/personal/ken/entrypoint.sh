#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Personal entrypoint — clones dotfiles via the host SSH agent on first start,
# then runs the dotfiles install script. Uses a marker file so install only
# runs once per container lifecycle.
#
# Bind-mounted .claude/* paths are skipped by chezmoi via .chezmoiignore in
# the dotfiles repo (conditional on REMOTE_CONTAINERS=true).
#
# Re-trigger: rm ~/.dotfiles-installed (and rm -rf ~/.dotfiles to re-clone).
# =============================================================================

MARKER="$HOME/.dotfiles-installed"
DOTFILES_DIR="$HOME/.dotfiles"
DOTFILES_REPO="${DOTFILES_REPO:-git@github.com:kenfdev/dotfiles.git}"

if [ ! -d "$DOTFILES_DIR/.git" ]; then
    if [ -n "${SSH_AUTH_SOCK:-}" ] && [ -S "$SSH_AUTH_SOCK" ]; then
        echo "[personal] Cloning dotfiles from $DOTFILES_REPO..."
        mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
        ssh-keyscan -t ed25519,rsa github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null || true
        git clone "$DOTFILES_REPO" "$DOTFILES_DIR"
    else
        echo "[personal] No SSH agent forwarded (SSH_AUTH_SOCK unset/invalid) — skipping dotfiles clone."
    fi
fi

if [ ! -f "$MARKER" ] && [ -d "$DOTFILES_DIR/scripts" ]; then
    echo "[personal] Running dotfiles setup..."
    REMOTE_CONTAINERS=true bash "$DOTFILES_DIR/scripts/install.sh"
    touch "$MARKER"
    echo "[personal] Dotfiles setup complete."
else
    [ -f "$MARKER" ] && echo "[personal] Dotfiles already installed (remove $MARKER to re-run)."
    [ ! -d "$DOTFILES_DIR/scripts" ] && echo "[personal] No dotfiles found at $DOTFILES_DIR — skipping install."
fi

exec "$@"
