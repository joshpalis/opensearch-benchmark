# mode:
# latest (i.e. git fetch origin && git checkout master && git rebase origin/master,
# current (just use source tree as is)
# replay (for backtesting), can get a range of dates or commits

# later:
# tournament: checking two revisions against each other
import logging

from rally import config, driver, exceptions, paths, sweeper, summary_reporter
from rally.mechanic import mechanic
from rally.track import track, geonames_track
from rally.utils import process

logger = logging.getLogger("rally.racecontrol")


class RaceControl:
    COMMANDS = {
        'all': lambda: [RacingTeam(), Press(report_only=False)],
        'race': lambda: [RacingTeam()],
        'report': lambda: [Press(report_only=True)]
    }

    def __init__(self, cfg):
        self._config = cfg

    def start(self, command):
        participants = self._choose_participants(command)

        for p in participants:
            p.prepare(self._all_tracks(), self._config)

        for track in self._all_tracks():
            for p in participants:
                p.do(track)

        print("\nAll tracks done.")
        sweeper.Sweeper(self._config).run()

    def _choose_participants(self, command):
        logger.info("Executing command [%s]" % command)
        try:
            return RaceControl.COMMANDS[command]()
        except KeyError:
            raise exceptions.ImproperlyConfigured("Unknown command [%s]" % command)

    def _all_tracks(self):
        # just one track for now
        return [geonames_track.geonamesTrackSpec]


class RacingTeam:
    def __init__(self):
        self._mechanic = None
        self._driver = None
        self._marshal = None
        self._config = None

    def prepare(self, tracks, cfg):
        self._config = cfg
        self._mechanic = mechanic.Mechanic(cfg)
        self._driver = driver.Driver(cfg)
        self._marshal = track.Marshal(cfg)
        self._mechanic.prepare_candidate()
        print("Racing on %d track(s). Overall ETA: %d minutes (depending on your hardware)\n" % (len(tracks), self._eta(tracks)))

    def do(self, track):
        selected_setups = self._config.opts("benchmarks", "tracksetups.selected")
        # we're very specific which nodes we kill as there is potentially also an Elasticsearch based metrics store running on this machine
        node_prefix = self._config.opts("provisioning", "node.name.prefix")
        process.kill_running_es_instances(node_prefix)
        self._marshal.setup(track)
        race_paths = paths.Paths(self._config)
        track_root = race_paths.track_root(track.name)
        self._config.add(config.Scope.benchmark, "system", "track.root.dir", track_root)

        for track_setup in track.track_setups:
            if track_setup.name in selected_setups:
                self._config.add(config.Scope.trackSetup, "system", "track.setup.root.dir",
                                 race_paths.track_setup_root(track.name, track_setup.name))
                self._config.add(config.Scope.trackSetup, "system", "track.setup.log.dir",
                                 race_paths.track_setup_logs(track.name, track_setup.name))
                print("Racing on track '%s' with setup '%s'" % (track.name, track_setup.name))
                logger.info("Racing on track [%s] with setup [%s]" % (track.name, track_setup.name))
                cluster = self._mechanic.start_engine(track, track_setup)
                self._driver.setup(cluster, track, track_setup)
                self._driver.go(cluster, track, track_setup)
                self._mechanic.stop_engine(cluster)
                self._driver.tear_down(track, track_setup)
                self._mechanic.revise_candidate()
            else:
                logger.debug("Skipping track setup [%s] (not selected)." % track_setup.name)

    def _eta(self, tracks):
        eta = 0
        for track in tracks:
            eta += track.estimated_benchmark_time_in_minutes
        return eta


class Press:
    def __init__(self, report_only):
        self._summary_reporter = None
        self.report_only = report_only

    def prepare(self, tracks, config):
        self._summary_reporter = summary_reporter.SummaryReporter(config)

    def do(self, track):
        self._summary_reporter.report(track)
