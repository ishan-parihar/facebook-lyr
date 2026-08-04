#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────
# Facebook / Messenger MCP Server — Install Script
# Cross-platform (Linux / macOS) — idempotent, safe to rerun
# Usage: curl -fsSL <url> | bash
# ─────────────────────────────────────────────────────────

# ── Colors ────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
step()    { echo -e "\n${CYAN}▸ $*${NC}"; }

# ── Detect OS ────────────────────────────────────────────
detect_os() {
    case "$(uname -s)" in
        Linux*)  OS="linux" ;;
        Darwin*) OS="macos" ;;
        *)       error "Unsupported OS: $(uname -s)"; exit 1 ;;
    esac
    info "Detected OS: ${OS}"
}

# ── 1. Check Prerequisites ────────────────────────────────
check_prerequisites() {
    step "Checking prerequisites"

    # Git
    if command -v git &>/dev/null; then
        success "git $(git --version | awk '{print $3}')"
    else
        error "git is not installed."
        echo "  Install:  apt install git   (Linux)  |  brew install git   (macOS)"
        exit 1
    fi

    # Python >= 3.11
    if command -v python3 &>/dev/null; then
        PY_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
        PY_MAJOR="$(echo "$PY_VERSION" | cut -d. -f1)"
        PY_MINOR="$(echo "$PY_VERSION" | cut -d. -f2)"
        if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 11 ]; then
            success "python ${PY_VERSION}"
        else
            error "Python 3.11+ required (found ${PY_VERSION})."
            echo "  Install:  uv python install 3.11"
            exit 1
        fi
    else
        warn "python3 not found — uv will manage Python."
    fi

    # uv
    if command -v uv &>/dev/null; then
        success "uv $(uv --version)"
        UV_INSTALLED=false
    else
        warn "uv not found — will install."
        UV_INSTALLED=true
    fi
}

# ── 2. Install uv ─────────────────────────────────────────
install_uv() {
    if [ "$UV_INSTALLED" = false ]; then
        return
    fi

    step "Installing uv"
    if curl -LsSf https://astral.sh/uv/install.sh | sh; then
        if [ -d "$HOME/.cargo/bin" ] && ! echo "$PATH" | grep -q "$HOME/.cargo/bin"; then
            export PATH="$HOME/.cargo/bin:$PATH"
        fi
        if [ -d "$HOME/.local/bin" ] && ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
            export PATH="$HOME/.local/bin:$PATH"
        fi
        success "uv installed: $(uv --version)"
    else
        error "Failed to install uv."
        echo "  Try manually:  curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
}

# ── 3. Locate or clone the repo ──────────────────────────
REPO_URL="https://github.com/ishan-parihar/facebook-lyr"
REPO_NAME="facebook-lyr"

locate_or_clone_repo() {
    step "Locating repository"

    if [ -f "pyproject.toml" ] && [ -d "facebook_mcp_server" ]; then
        REPO_DIR="$(pwd)"
        success "Already in repo: ${REPO_DIR}"
        return
    fi

    for candidate in \
        "$HOME/projects/${REPO_NAME}" \
        "$HOME/${REPO_NAME}" \
        "$HOME/dev/${REPO_NAME}" \
        "$HOME/code/${REPO_NAME}"; do
        if [ -f "${candidate}/pyproject.toml" ]; then
            REPO_DIR="${candidate}"
            success "Found repo: ${REPO_DIR}"
            return
        fi
    done

    REPO_DIR="$HOME/projects/${REPO_NAME}"
    if [ -d "$REPO_DIR" ]; then
        success "Repo already exists: ${REPO_DIR}"
        return
    fi

    info "Cloning repo to ${REPO_DIR} ..."
    mkdir -p "$(dirname "$REPO_DIR")"
    if git clone "${REPO_URL}" "${REPO_DIR}"; then
        success "Cloned to ${REPO_DIR}"
    else
        error "Failed to clone repository."
        echo "  Try manually:  git clone ${REPO_URL} ${REPO_DIR}"
        exit 1
    fi
}

# ── 4. Install Dependencies ──────────────────────────────
install_dependencies() {
    step "Installing dependencies"
    cd "${REPO_DIR}"

    if uv sync; then
        success "Dependencies installed"
    else
        error "uv sync failed."
        echo "  Try:  cd ${REPO_DIR} && uv sync --reinstall"
        exit 1
    fi
}

# ── 5. Validate Installation ─────────────────────────────
validate_installation() {
    step "Validating installation"
    cd "${REPO_DIR}"

    if uv run python -c "import facebook_mcp_server; print('  Module imported successfully')" 2>/dev/null; then
        success "Module import OK"
    else
        error "Failed to import facebook_mcp_server."
        echo "  Try:  cd ${REPO_DIR} && uv sync --reinstall"
        exit 1
    fi
}

# ── 6. Check Facebook session ────────────────────────────
check_facebook_session() {
    step "Checking Facebook session"

    COOKIES_PATH="$HOME/.facebook-lyr/profile/cookies.json"
    if [ -f "$COOKIES_PATH" ]; then
        success "Existing cookie session found at ${COOKIES_PATH}"
        echo "  Validate it with:  uv run facebook-lyr --status"
    elif [ -n "${FACEBOOK_COOKIES:-}" ]; then
        success "FACEBOOK_COOKIES is set (env)"
    else
        warn "No Facebook session found."
        echo "  First run:  uv run facebook-lyr --login"
        echo "  (or export FACEBOOK_COOKIES for headless use)"
    fi
}

# ── 7. Print MCP Configs ─────────────────────────────────
print_mcp_configs() {
    step "MCP Client Configurations"

    echo -e "  ${YELLOW}Copy the config below into your MCP client.${NC}"
    echo ""

    # Claude Desktop / Cursor / Windsurf
    echo -e "  ${CYAN}Claude Desktop / Cursor / Windsurf${NC}:"
    echo '```json'
    echo '{'
    echo '  "mcpServers": {'
    echo '    "facebook": {'
    echo '      "command": "uv",'
    echo '      "args": ["run", "-m", "facebook_mcp_server"],'
    echo '      "cwd": "'"${REPO_DIR}"'",'
    echo '      "env": { "FACEBOOK_COOKIES": "{\"c_user\":\"...\",\"xs\":\"...\"}" }'
    echo '    }'
    echo '  }'
    echo '}'
    echo '```'
    echo ""

    # uvx (no clone needed, once published)
    echo -e "  ${CYAN}Any MCP client (uvx — no clone needed):${NC}"
    echo '```json'
    echo '{'
    echo '  "mcpServers": {'
    echo '    "facebook": {'
    echo '      "command": "uvx",'
    echo '      "args": ["facebook-lyr"]'
    echo '    }'
    echo '  }'
    echo '}'
    echo '```'
    echo ""
}

# ── 8. Print Next Steps ──────────────────────────────────
print_next_steps() {
    step "Next Steps"

    echo -e "  ${GREEN}1. Add to your MCP client${NC}"
    echo "     Copy one of the configs above into your MCP client's config file."
    echo ""
    echo -e "  ${GREEN}2. First-time login${NC}"
    echo "       cd ${REPO_DIR}"
    echo "       uv run facebook-lyr --login"
    echo ""
    echo -e "  ${GREEN}3. Test the installation${NC}"
    echo "       uv run facebook-lyr --status"
    echo ""
    echo -e "  ${GREEN}4. Debug (if needed)${NC}"
    echo "       uv run -m facebook_mcp_server --log-level DEBUG"
    echo ""
}

# ── Main ──────────────────────────────────────────────────
main() {
    echo ""
    echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║${NC}  ${BLUE}Facebook / Messenger MCP Server — Installer${NC}   ${GREEN}║${NC}"
    echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""

    detect_os
    check_prerequisites
    install_uv
    locate_or_clone_repo
    install_dependencies
    validate_installation
    check_facebook_session
    print_mcp_configs
    print_next_steps

    echo -e "${GREEN}Installation complete!${NC}"
    echo ""
}

main "$@"
