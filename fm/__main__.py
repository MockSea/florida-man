"""Run the server: python3 -m fm"""
import sys

from . import config, server, source
from .cache import Store
from .gnews_decode import Decoder
from .publisher import Fetcher
from .service import FloridaMan
from .summaries import Summaries
from .warm import RandomStash, Warmer


def main():
    cfg = config.Config.from_env()
    store = Store(cfg.data_dir)
    store.purge_expired()
    summaries = Summaries(store, Decoder(), Fetcher(), per_minute=cfg.summaries_per_minute)
    app = FloridaMan(
        store,
        tz=cfg.tz,
        limiter=source.RateLimiter(cfg.fetches_per_minute),
        home_years=cfg.home_years,
        search_years=cfg.search_years,
        search_ttl=cfg.search_ttl,
        summaries=summaries,
    )
    if summaries.start():  # daemon thread; None when FM_SUMMARIES_PER_MINUTE=0
        # Warming only makes sense with summaries to warm.
        app.stash = RandomStash(app)
        Warmer(app, app.stash).start()
    httpd = server.serve(app, config.HOST, cfg.port, cfg.static_dir)
    sys.stderr.write("Florida Man of the Day on http://%s:%d (data: %s)\n"
                     % (config.HOST, cfg.port, cfg.data_dir))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        store.close()


if __name__ == "__main__":
    main()
