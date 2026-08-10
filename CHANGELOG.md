# Changelog

## 1.1.0 - 2026-08-10

- Add fast segmented hashing with full-hash collision confirmation for substantially faster large-library scans.
- Add duplicate shadow records, confirmed-duplicate build skipping, and in-place promotion when source folders are removed.
- Add batched audio extraction and consistent progress monitoring for scans, builds, undo, and library-layout operations.
- Move large sessions to DB-backed table, tree, and sound-map views with bounded hydration, lazy loading, and prewarmed view caches.
- Expand custom library structures with filter-backed nodes, semantic drag-and-drop reclassification, and faster draft application and discard.
- Improve library navigation, saved duplicate filters, audio preview controls, themes, update notifications, and cross-platform packaging.
- Fix session restore, rescan, build, undo, database lifecycle, and target-cleanup regressions.

## 1.0.2 - 2026-06-22

- Fix play/pause icon swap logic in `AnimatedIconButton` by checking and rendering dynamic icons set via `setIcon()`.
- Fix playback rewind on resume in `SoundPreviewPlayer.toggle_play_pause()` when the player is at `EndOfMedia` or has reached its duration.
- Fix the export icon's mouse grab conflict and "sticky drag" glitch by accepting `LeftButton` mouse press events in `DragOutIconButton` without propagating to the base button.
- Unify the export icon's drag UX with table and tree views by adding a transparent drag pixmap.
- Clean up the redundant `setIcon` call on the stop button in `_update_play_pause_icon()`.

## 1.0.1 - 2026-06-22

- Fix PyInstaller packaging dependency for `backports` namespace packages (resolving Linux launch crash).
- Package missing GUI theme stylesheet files in PyInstaller binary output.
- Reduce C++ extractor maximum audio decoding cap from 60s to 20s to prevent excessive CPU/memory usage.
- Centralize scan concurrency limits and introduce bounded task submission queueing to resolve macOS userspace watchdog panics.
- Add MAC address and hostname fallback comparisons to engine lock negotiation to prevent false-positive lock blocks after reboots or DHCP renames.
- Add checkable "High Performance Scan" option under the Library menu and Launcher to toggle low-resource scan worker limits.
- Fix scan monitor window background rendering white by enabling `Qt.WA_StyledBackground`.
- Fix launcher folder registration when multiple folders are processed.

## 1.0.0 - 2026-06-11

- First public release of Unshuffle.
