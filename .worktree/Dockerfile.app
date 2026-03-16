# =============================================================================
# Default app Dockerfile. Inherits from the team-shared base image.
#
# Python 3.12 development environment using uv.
#
# The base image is built by the `base` service in docker-compose.yml.
# The `depends_on: base` ensures it is built before this Dockerfile runs.
# =============================================================================

FROM fdsx-dev-base:local

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Make uv available on PATH
ENV PATH="/home/vscode/.local/bin:$PATH"

# Install Python 3.12 via uv
RUN uv python install 3.12
