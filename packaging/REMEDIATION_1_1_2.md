# Patch release validation for issues #17 and #18

The rendering correction is reproducible at fractional and Retina display scaling.
The Linux SIGILL report points to host-specific HNSW compilation; the affected CPU
and faulting instruction have not yet been obtained. Do not describe that diagnosis
as confirmed solely from the stack trace.

## Automated gates

Use Python 3.14 and `python scripts/install_release_dependencies.py` in a dedicated
build environment. This pins the packaged HNSW build to 0.8.0, disables native CPU
targeting, forces uncached source compilation, and writes compiler output,
dependency versions, and the extension checksum to `dist/build-metadata/`.
The application's dependency range and analysis behavior are unchanged.

On Linux, install `xvfb xauth xdotool qemu-user` alongside the existing Qt/audio
dependencies, then run:

```bash
python scripts/check_native_extractor_bundle.py
python scripts/check_linux_bundle.py --app dist/Unshuffle/Unshuffle
python scripts/check_linux_bundle.py --appimage dist/installer/Unshuffle-1.1.1-x86_64.AppImage
```

The bundle checker runs the native index self-test on the host and QEMU's Nehalem
CPU (without AVX), checks the headless diagnostic, and opens and dismisses the real
launcher under Xvfb. Each invocation has a timeout and uses temporary application
data. The AppImage check extracts the final image and tests its bundled ELF.
The original AppImage mounting runtime is not executed under CPU emulation.

Retain build metadata with candidate installers. Match the self-test's HNSW
checksum to `dependencies.json` so the extension tested is the extension built.

CI builds the frozen bundle on Ubuntu 24.04 and runs the mandatory Nehalem check
there. Only after that succeeds is the same bundle archived (preserving executable
permissions) and tested on Ubuntu 26.04 with `--host-only`. That second job checks
the already CPU-validated artifact against the newer host runtime; it does not
produce a different build. Release and AppImage gates keep CPU emulation enabled.
User-mode QEMU also loads host system libraries, so a failure of a separately built
Ubuntu 26.04 bundle cannot be attributed to HNSW from SIGILL alone.

The native self-test flushes import/index/query stages to stderr before each step;
stdout remains a JSON result. If no entrypoint marker is printed before a native
crash, execution failed before reaching the Python diagnostic branch. Keep the
QEMU failure fatal rather than retrying under a newer CPU model.

The X11 smoke sends Escape's press only: QDialog can destroy its window before
the release event of a full `xdotool key` command. It still requires both a visible
launcher and a clean process exit. Missing windows, failed key delivery, crashes,
and timeouts remain failures.

## Required manual gates before versioning/publishing 1.1.2

- Install the macOS candidate on a Retina display. Select a disposable audio
  sample; test clicks and Space, pause/resume, stop, natural completion, theme
  changes, and moving between displays. Icons must keep their logical size and
  remain centered; button geometry and shortcuts must stay unchanged.
- On openSUSE Tumbleweed, run the candidate AppImage, then exercise preview,
  similarity/coherence, and duplicate detection on a disposable sample library.
- Use copies of application data for these checks. Do not delete or repair user
  databases, regenerate feature vectors, or change taxonomy/scoring settings.
- Run the full test suite and build all platform installers. Keep the existing
  package identities and extractor protocol version.
- Only after these gates pass, synchronize the 1.1.2 version across application
  metadata, installers, workflows, tests, and release notes. Retain the 1.1.1
  artifacts and checksums for comparison. Do not overwrite an existing release.

## Confirming the original SIGILL

On the affected machine, record `lscpu` output and CPU flags. Keep the exact 1.1.1
AppImage and a copy of the core dump; an unrelated rebuild cannot establish which
instruction faulted. No user library contents are needed for instruction analysis.

```bash
./Unshuffle-1.1.1-x86_64.AppImage --appimage-extract
sha256sum squashfs-root/_internal/hnswlib*.so
objdump -d --start-address=0x1935a --stop-address=0x1939a squashfs-root/_internal/hnswlib*.so
coredumpctl debug <crashed-process-id>
```

In GDB, use `x/8i $pc`, `info registers`, and `bt` to identify the actual faulting
instruction. The reported extension offset is `0x1937a`; verify its mapping against
the exact binary. Compare the instruction's required CPU features with `lscpu`,
then reproduce on a matching VM or QEMU CPU model. A static disassembly alone
does not prove which instruction executed or which CPU feature was absent.

Do not attempt to catch SIGILL with Python exceptions. Runtime process isolation
and similarity-backend replacement are outside this patch.
