# Unified Scheduler control-plane images 1.5.4

This Git LFS artifact contains the release images built and verified on 2026-07-29.

## Images

| Image | Image ID |
|---|---|
| `gpu-control-api:1.5.4` | `sha256:06147d527d4a146141c9cf3c56b62c474096543cbdbde2050b2d1a652e478cb3` |
| `unified-scheduler-asset-api:1.5.4` | `sha256:827053b49248ea22296fb3b78fb3012f1a158577f34921b30dcf140567ce0c3d` |
| `gpu-control-scheduler:1.5.4` | `sha256:f9569a39438bbbc63a9b3f8c6ff3991e1bce67efddc69167467549c16f4a227b` |
| `gpu-control-web:1.5.4` | `sha256:8f9558646a306600a24c2898355901a85b0e3b4fd94c3e807b7d2fa27cf408ae` |

The archive contains application images only. It does not include models, production jobs, databases, Docker volumes, client uploads, worker secrets, TLS private keys, or external business repositories.

## Reassemble and verify

```bash
cat unified-scheduler-1.5.4-images.tar.gz.part-* > /tmp/unified-scheduler-1.5.4-images.tar.gz
sha256sum -c SHA256SUMS.txt
gzip -t /tmp/unified-scheduler-1.5.4-images.tar.gz
docker load -i /tmp/unified-scheduler-1.5.4-images.tar.gz
```

Archive size: `190465348` bytes. Archive SHA-256: `b3afe81e660f899f737819deabd46bd5c9dba847097df806a87b66ca79a94d51`.

## Production rollout note

Asset API and WebUI 1.5.4 were rolled out and verified healthy after confirming the asset queue was empty. The GPU API and scheduler images were built, tested, and archived, but were not restarted during active GPU production work.
