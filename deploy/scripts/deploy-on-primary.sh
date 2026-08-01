#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "Usage: $0 <build-number> <commit-short> <source-archive> <run-tests>" >&2
  exit 2
fi

build_number="$1"
commit_short="$2"
source_archive="$3"
run_tests="$4"

if [[ ! "${build_number}" =~ ^[0-9]+$ ]]; then
  echo "build-number must be numeric" >&2
  exit 3
fi
if [[ ! "${commit_short}" =~ ^[0-9a-f]{7,40}$ ]]; then
  echo "commit-short must be a hexadecimal Git revision" >&2
  exit 4
fi
if [[ ! -f "${source_archive}" ]]; then
  echo "source archive does not exist: ${source_archive}" >&2
  exit 5
fi
if [[ "${run_tests}" != "true" && "${run_tests}" != "false" ]]; then
  echo "run-tests must be true or false" >&2
  exit 6
fi

image="linkparse"
tag="${commit_short}-b${build_number}"
test_image="${image}:test-${commit_short}-b${build_number}"
deploy_dir="/opt/tolink/linkparse"
runtime_env="${deploy_dir}/.env"
work_root="/opt/tolink/jenkins/workspaces"
build_dir="${work_root}/linkparse-${build_number}"
lock_file="${deploy_dir}/.jenkins-deploy.lock"
http_url="http://100.86.10.52:18743"

cleanup() {
  docker image rm -f "${test_image}" >/dev/null 2>&1 || true
  if [[ "${build_dir}" == "${work_root}/linkparse-${build_number}" ]]; then
    rm -rf -- "${build_dir}"
  fi
}
trap cleanup EXIT

mkdir -p "${work_root}" "${deploy_dir}"
exec 9>"${lock_file}"
flock -w 1800 9 || {
  echo "Timed out waiting for LinkParse deployment lock" >&2
  exit 7
}

if [[ ! -f "${runtime_env}" ]]; then
  echo "Missing runtime env file: ${runtime_env}" >&2
  exit 10
fi
if [[ "$(stat -c '%a' "${runtime_env}")" != "600" ]]; then
  echo "Runtime env file must use mode 600" >&2
  exit 11
fi
shared_network="$(sed -n 's/^LINKPARSE_SHARED_NETWORK=//p' "${runtime_env}" | tail -n 1)"
shared_network="${shared_network:-link_tolink-net}"
docker network inspect "${shared_network}" >/dev/null

rm -rf -- "${build_dir}"
mkdir -p "${build_dir}"
tar -xzf "${source_archive}" -C "${build_dir}"
mkdir -p "${build_dir}/wheelhouse"
if [[ -d "${deploy_dir}/wheelhouse" ]]; then
  cp -al "${deploy_dir}/wheelhouse/." "${build_dir}/wheelhouse/"
fi

if [[ "${run_tests}" == "true" ]]; then
  DOCKER_BUILDKIT=1 docker build \
    --target test \
    --label "org.opencontainers.image.revision=${commit_short}" \
    -t "${test_image}" \
    "${build_dir}"
fi

DOCKER_BUILDKIT=1 docker build \
  --target production \
  --label "org.opencontainers.image.revision=${commit_short}" \
  --label "org.opencontainers.image.source=https://github.com/ql-link/LinkParse" \
  -t "${image}:${tag}" \
  "${build_dir}"

docker run --rm \
  --network "${shared_network}" \
  --env-file "${runtime_env}" \
  "${image}:${tag}" \
  python -c 'import os; from redis import Redis; assert Redis.from_url(os.environ["LINKPARSE_REDIS_URL"]).ping()'

install -m 0644 "${build_dir}/docker-compose.yml" "${deploy_dir}/docker-compose.yml"
install -m 0644 "${build_dir}/nginx.conf" "${deploy_dir}/nginx.conf"
install -m 0644 \
  "${build_dir}/deploy/systemd/linkparse-after-tailscale.service" \
  /etc/systemd/system/linkparse-after-tailscale.service
systemctl daemon-reload
systemctl enable linkparse-after-tailscale.service

if grep -q '^LINKPARSE_IMAGE=' "${runtime_env}"; then
  sed -i "s|^LINKPARSE_IMAGE=.*$|LINKPARSE_IMAGE=${image}:${tag}|" "${runtime_env}"
else
  printf '\nLINKPARSE_IMAGE=%s:%s\n' "${image}" "${tag}" >>"${runtime_env}"
fi
chmod 600 "${runtime_env}"

docker compose --project-directory "${deploy_dir}" \
  -f "${deploy_dir}/docker-compose.yml" \
  up -d --no-build --remove-orphans

for _ in $(seq 1 45); do
  health_status="$(docker inspect --format='{{.State.Health.Status}}' linkparse-api-1 2>/dev/null || true)"
  if [[ "${health_status}" == "healthy" ]] && \
    curl -fsS "${http_url}/health" >/dev/null; then
    cat >"${deploy_dir}/.deployment" <<EOF
image=${image}:${tag}
revision=${commit_short}
build=${build_number}
deployed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
    chmod 0644 "${deploy_dir}/.deployment"
    echo "Container health: ${health_status}"
    echo "linkparse deployed: ${image}:${tag}"
    exit 0
  fi
  sleep 2
done

docker compose --project-directory "${deploy_dir}" \
  -f "${deploy_dir}/docker-compose.yml" logs --tail=120 api worker nginx
echo "LinkParse health check timed out." >&2
exit 12
