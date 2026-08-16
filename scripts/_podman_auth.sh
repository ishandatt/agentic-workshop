# Sourced by db.sh and n8n.sh. Not executable on its own.
#
# Both of this workshop's images are public and need no credentials — but Podman
# still walks EVERY `credHelpers` entry in ~/.docker/config.json when it
# assembles auth, not just one matching the registry it is about to talk to. So
# a laptop configured for Google Container Registry fails an anonymous docker.io
# pull with:
#
#   Error: error getting credentials - err: exec: "docker-credential-gcloud":
#          executable file not found in $PATH
#
# That is a very common setup anywhere using GCP, and for db.sh it lands at step
# 5 of setup.sh — AFTER the ~5 GB model download — blaming a Google binary that
# has nothing to do with this workshop. Pointing REGISTRY_AUTH_FILE at an empty
# auths file skips helper resolution entirely.
#
# Respect the variable if the user already set one: someone pulling from a
# private mirror needs their real credentials.
if [ -z "${REGISTRY_AUTH_FILE:-}" ]; then
  _EMPTY_AUTH="${TMPDIR:-/tmp}/workshop-empty-auth.json"
  [ -f "$_EMPTY_AUTH" ] || echo '{"auths":{}}' > "$_EMPTY_AUTH"
  export REGISTRY_AUTH_FILE="$_EMPTY_AUTH"
fi
