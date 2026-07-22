#!/usr/bin/env bash
set -Eeuo pipefail

control_ip=""
dns_name=""
force=false
while (($#)); do
  case "$1" in
    --control-ip) control_ip="$2"; shift 2 ;;
    --dns) dns_name="$2"; shift 2 ;;
    --force) force=true; shift ;;
    -h|--help) echo "用法: $0 --control-ip IP [--dns NAME] [--force]"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done
[[ -n "${control_ip}" ]] || { echo "必须提供 --control-ip" >&2; exit 2; }
command -v openssl >/dev/null || { echo "缺少 openssl" >&2; exit 1; }

target="deploy/control-plane/nginx/certs"
mkdir -p "${target}"
if [[ "${force}" != true && ( -e "${target}/server.crt" || -e "${target}/server.key" ) ]]; then
  echo "证书已存在；如需替换请使用 --force" >&2
  exit 1
fi
umask 077
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "${target}/lan-ca.key"
openssl req -x509 -new -key "${target}/lan-ca.key" -sha256 -days 3650 \
  -subj "/CN=GPU Control LAN CA" -out "${target}/lan-ca.crt"
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "${target}/server.key"
san="IP:${control_ip}"
[[ -n "${dns_name}" ]] && san="${san},DNS:${dns_name}"
openssl req -new -key "${target}/server.key" -subj "/CN=${dns_name:-$control_ip}" \
  -addext "subjectAltName=${san}" -out "${target}/server.csr"
openssl x509 -req -in "${target}/server.csr" -CA "${target}/lan-ca.crt" \
  -CAkey "${target}/lan-ca.key" -CAcreateserial -days 825 -sha256 \
  -copy_extensions copy -out "${target}/server.crt"
chmod 0600 "${target}/server.key" "${target}/lan-ca.key"
chmod 0644 "${target}/server.crt" "${target}/lan-ca.crt"

cert_digest="$(openssl x509 -in "${target}/server.crt" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | cut -d' ' -f1)"
key_digest="$(openssl pkey -in "${target}/server.key" -pubout -outform DER | sha256sum | cut -d' ' -f1)"
[[ "${cert_digest}" == "${key_digest}" ]] || { echo "证书与私钥不匹配" >&2; exit 1; }
rm -f "${target}/server.csr" "${target}/lan-ca.srl"
echo "TLS 已生成：${target}/server.crt"
echo "把 ${target}/lan-ca.crt 导入管理电脑信任库，或 curl --cacert 使用。"
