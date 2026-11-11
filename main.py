"""Точка входа.

  ImperialLauncher.exe                 — окно лаунчера
  ImperialLauncher.exe --sync <папка>  — тихое обновление (для PreLaunchCommand Freesm)
  ImperialLauncher.exe --update        — обновить без окна и показать ход в консоли
  ImperialLauncher.exe --dry-run       — показать, что изменилось бы, ничего не меняя
"""
import argparse
import sys

from launcher import __version__, core, freesm, settings, syncer


def _out(msg: str) -> None:
    if sys.stdout:  # в exe без консоли stdout может быть None
        try:
            print(msg, flush=True)
        except Exception:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ImperialLauncher", description="Лаунчер сборки «Империя»")
    ap.add_argument("--sync", metavar="ПАПКА_ЭКЗЕМПЛЯРА", help="тихо обновить сборку (вызывается из Freesm)")
    ap.add_argument("--update", action="store_true", help="обновить без окна")
    ap.add_argument("--dry-run", action="store_true", help="только показать план")
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)

    if args.version:
        _out(__version__)
        return 0

    lg = core.setup_logging()

    def log(msg: str) -> None:
        lg.info(msg)
        _out(msg)

    s = settings.load()

    if args.sync:
        core.run_headless_sync(s, args.sync, log)
        return 0  # ошибка обновления не должна блокировать запуск игры

    if args.update or args.dry_run:
        try:
            m = core.fetch_manifest(s)
            if args.dry_run:
                _root, _inst, game = core.paths_for(s, m)
                opts = core.sync_options(s)
                opts.dry_run = True
                res = syncer.sync(m, game, opts, log=log)
                for a in res.plan:
                    if a.kind in ("download", "delete", "quarantine"):
                        _out(f"  {a.kind:10s} {a.path} {a.reason}")
                return 0

            def prog(done, total, name):
                if total:
                    _out(f"\r{name}: {done * 100 // total}%")

            res = core.run_update(s, log, prog, manifest=m)
            return 0 if res.ok else 1
        except core.CoreError as e:
            _out(f"Ошибка: {e}")
            return 1

    from launcher.gui import run
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
