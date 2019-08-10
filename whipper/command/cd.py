# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4

# Copyright (C) 2009 Thomas Vander Stichele

# This file is part of whipper.
#
# whipper is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# whipper is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with whipper.  If not, see <http://www.gnu.org/licenses/>.

import argparse
import cdio
import os
import glob
import logging
from whipper.command.basecommand import BaseCommand
from whipper.common import (
    accurip, config, drive, program, task
)
from whipper.common.common import validate_template
from whipper.program import cdrdao, cdparanoia, utils
from whipper.result import result

logger = logging.getLogger(__name__)


SILENT = 0
MAX_TRIES = 5

DEFAULT_TRACK_TEMPLATE = '%r/%A - %d/%t. %a - %n'
DEFAULT_DISC_TEMPLATE = '%r/%A - %d/%A - %d'

TEMPLATE_DESCRIPTION = '''
Tracks are named according to the track template, filling in the variables
and adding the file extension. Variables exclusive to the track template are:
 - %t: track number
 - %a: track artist
 - %n: track title
 - %s: track sort name

Disc files (.cue, .log, .m3u) are named according to the disc template,
filling in the variables and adding the file extension. Variables for both
disc and track template are:
 - %A: release artist
 - %S: release sort name
 - %d: disc title
 - %y: release year
 - %r: release type, lowercase
 - %R: release type, normal case
 - %x: audio extension, lowercase
 - %X: audio extension, uppercase

'''


class _CD(BaseCommand):
    eject = True

    # XXX: Pylint, parameters differ from overridden 'add_arguments' method
    @staticmethod
    def add_arguments(parser):
        parser.add_argument('-R', '--release-id',
                            action="store", dest="release_id",
                            help="MusicBrainz release id to match to "
                            "(if there are multiple)")
        parser.add_argument('-p', '--prompt',
                            action="store_true", dest="prompt",
                            help="Prompt if there are multiple "
                            "matching releases")
        parser.add_argument('-c', '--country',
                            action="store", dest="country",
                            help="Filter releases by country")

    def do(self):
        self.config = config.Config()
        self.program = program.Program(self.config,
                                       record=self.options.record)
        self.runner = task.SyncRunner()

        # if the device is mounted (data session), unmount it
        self.device = self.options.device
        logger.info('checking device %s', self.device)

        utils.load_device(self.device)
        utils.unmount_device(self.device)

        # first, read the normal TOC, which is fast
        self.ittoc = self.program.getFastToc(self.runner, self.device)

        # already show us some info based on this
        self.program.getRipResult(self.ittoc.getCDDBDiscId())
        print("CDDB disc id: %s" % self.ittoc.getCDDBDiscId())
        self.mbdiscid = self.ittoc.getMusicBrainzDiscId()
        print("MusicBrainz disc id %s" % self.mbdiscid)

        print("MusicBrainz lookup URL %s" %
              self.ittoc.getMusicBrainzSubmitURL())

        self.program.metadata = (
            self.program.getMusicBrainz(self.ittoc, self.mbdiscid,
                                        release=self.options.release_id,
                                        country=self.options.country,
                                        prompt=self.options.prompt)
        )

        if not self.program.metadata:
            # fall back to FreeDB for lookup
            cddbid = self.ittoc.getCDDBValues()
            cddbmd = self.program.getCDDB(cddbid)
            if cddbmd:
                logger.info('FreeDB identifies disc as %s', cddbmd)

            # also used by rip cd info
            if not getattr(self.options, 'unknown', False):
                logger.critical("unable to retrieve disc metadata, "
                                "--unknown argument not passed")
                return -1

        self.program.result.isCdr = cdrdao.DetectCdr(self.device)
        if (self.program.result.isCdr and
                not getattr(self.options, 'cdr', False)):
            logger.critical("inserted disc seems to be a CD-R, "
                            "--cdr not passed")
            return -1

        # Change working directory before cdrdao's task
        if getattr(self.options, 'working_directory', False):
            os.chdir(os.path.expanduser(self.options.working_directory))
        if hasattr(self.options, 'output_directory'):
            out_bpath = self.options.output_directory
            # Needed to preserve cdrdao's tocfile
            out_fpath = self.program.getPath(out_bpath,
                                             self.options.disc_template,
                                             self.mbdiscid,
                                             self.program.metadata)
        else:
            out_fpath = None
        # now, read the complete index table, which is slower
        offset = getattr(self.options, 'offset', 0)
        self.itable = self.program.getTable(self.runner,
                                            self.ittoc.getCDDBDiscId(),
                                            self.ittoc.getMusicBrainzDiscId(),
                                            self.device, offset, out_fpath)

        assert self.itable.getCDDBDiscId() == self.ittoc.getCDDBDiscId(), \
            "full table's id %s differs from toc id %s" % (
                self.itable.getCDDBDiscId(), self.ittoc.getCDDBDiscId())
        assert self.itable.getMusicBrainzDiscId() == \
            self.ittoc.getMusicBrainzDiscId(), \
            "full table's mb id %s differs from toc id mb %s" % (
            self.itable.getMusicBrainzDiscId(),
            self.ittoc.getMusicBrainzDiscId())

        if self.program.metadata:
            self.program.metadata.discid = self.ittoc.getMusicBrainzDiscId()

        # result

        self.program.result.cdrdaoVersion = cdrdao.version()
        self.program.result.cdparanoiaVersion = \
            cdparanoia.getCdParanoiaVersion()
        info = drive.getDeviceInfo(self.device)
        if info:
            try:
                self.program.result.cdparanoiaDefeatsCache = \
                    self.config.getDefeatsCache(*info)
            except KeyError as e:
                logger.debug('got key error: %r', (e, ))
        self.program.result.artist = self.program.metadata \
            and self.program.metadata.artist \
            or 'Unknown Artist'
        self.program.result.title = self.program.metadata \
            and self.program.metadata.title \
            or 'Unknown Title'
        _, self.program.result.vendor, self.program.result.model, \
            self.program.result.release = \
            cdio.Device(self.device).get_hwinfo()
        self.program.result.metadata = self.program.metadata

        self.doCommand()

        if (self.options.eject == 'success' and self.eject or
                self.options.eject == 'always'):
            utils.eject_device(self.device)

        return None

    def doCommand(self):
        pass


class Info(_CD):
    summary = "retrieve information about the currently inserted CD"
    description = ("Display MusicBrainz, CDDB/FreeDB, and AccurateRip "
                   "information for the currently inserted CD.")
    eject = False

    # Requires opts.device

    # XXX: Pylint, parameters differ from overridden 'add_arguments' method
    def add_arguments(self):
        _CD.add_arguments(self.parser)


class Rip(_CD):
    summary = "rip CD"
    # see whipper.common.program.Program.getPath for expansion
    description = """
Rips a CD.

%s

Paths to track files referenced in .cue and .m3u files will be made
relative to the directory of the disc files.

All files will be created relative to the given output directory.
Log files will log the path to tracks relative to this directory.
""" % TEMPLATE_DESCRIPTION
    formatter_class = argparse.RawTextHelpFormatter

    # Requires opts.record
    # Requires opts.device

    # XXX: Pylint, parameters differ from overridden 'add_arguments' method
    def add_arguments(self):
        loggers = list(result.getLoggers())
        default_offset = None
        info = drive.getDeviceInfo(self.opts.device)
        if info:
            try:
                default_offset = config.Config().getReadOffset(*info)
                logger.info("using configured read offset %d", default_offset)
            except KeyError:
                pass

        _CD.add_arguments(self.parser)

        self.parser.add_argument('-L', '--logger',
                                 action="store", dest="logger",
                                 default='whipper',
                                 help=("logger to use (choose from: '%s" %
                                       "', '".join(loggers) + "')"))
        self.parser.add_argument('-o', '--offset',
                                 action="store", dest="offset",
                                 default=default_offset,
                                 help="sample read offset")
        self.parser.add_argument('-x', '--force-overread',
                                 action="store_true", dest="overread",
                                 default=False,
                                 help="Force overreading into the "
                                 "lead-out portion of the disc. Works only "
                                 "if the patched cdparanoia package is "
                                 "installed and the drive "
                                 "supports this feature. ")
        self.parser.add_argument('-O', '--output-directory',
                                 action="store", dest="output_directory",
                                 default=os.path.relpath(os.getcwd()),
                                 help="output directory; will be included "
                                 "in file paths in log")
        self.parser.add_argument('-W', '--working-directory',
                                 action="store", dest="working_directory",
                                 help="working directory; whipper will "
                                 "change to this directory "
                                 "and files will be created relative to "
                                 "it when not absolute")
        self.parser.add_argument('--track-template',
                                 action="store", dest="track_template",
                                 default=DEFAULT_TRACK_TEMPLATE,
                                 help="template for track file naming")
        self.parser.add_argument('--disc-template',
                                 action="store", dest="disc_template",
                                 default=DEFAULT_DISC_TEMPLATE,
                                 help="template for disc file naming")
        self.parser.add_argument('-U', '--unknown',
                                 action="store_true", dest="unknown",
                                 help="whether to continue ripping if "
                                 "the CD is unknown", default=False)
        self.parser.add_argument('--cdr',
                                 action="store_true", dest="cdr",
                                 help="whether to continue ripping if "
                                 "the disc is a CD-R",
                                 default=False)

    def handle_arguments(self):
        self.options.output_directory = os.path.expanduser(
            self.options.output_directory)

        self.options.track_template = self.options.track_template
        validate_template(self.options.track_template, 'track')
        self.options.disc_template = self.options.disc_template
        validate_template(self.options.disc_template, 'disc')

        if self.options.offset is None:
            raise ValueError("Drive offset is unconfigured.\n"
                             "Please install pycdio and run 'whipper offset "
                             "find' to detect your drive's offset or set it "
                             "manually in the configuration file. It can "
                             "also be specified at runtime using the "
                             "'--offset=value' argument")

        if self.options.working_directory is not None:
            self.options.working_directory = os.path.expanduser(
                self.options.working_directory)

        if self.options.logger:
            try:
                self.logger = result.getLoggers()[self.options.logger]()
            except KeyError:
                msg = "No logger named %s found!" % self.options.logger
                logger.critical(msg)
                raise ValueError(msg)

    def doCommand(self):
        self.program.setWorkingDirectory(self.options.working_directory)
        self.program.outdir = self.options.output_directory
        self.program.result.offset = int(self.options.offset)
        self.program.result.overread = self.options.overread
        self.program.result.logger = self.options.logger

        discName = self.program.getPath(self.program.outdir,
                                        self.options.disc_template,
                                        self.mbdiscid,
                                        self.program.metadata)
        dirname = os.path.dirname(discName)
        if os.path.exists(dirname):
            logs = glob.glob(os.path.join(dirname, '*.log'))
            if logs:
                msg = ("output directory %s is a finished rip" % dirname)
                logger.debug(msg)
                raise RuntimeError(msg)
        else:
            logger.info("creating output directory %s", dirname)
            os.makedirs(dirname)

        # FIXME: turn this into a method

        def _ripIfNotRipped(number):
            logger.debug('ripIfNotRipped for track %d', number)
            # we can have a previous result
            trackResult = self.program.result.getTrackResult(number)
            if not trackResult:
                trackResult = result.TrackResult()
                self.program.result.tracks.append(trackResult)
            else:
                logger.debug('ripIfNotRipped have trackresult, path %r',
                             trackResult.filename)

            path = self.program.getPath(self.program.outdir,
                                        self.options.track_template,
                                        self.mbdiscid,
                                        self.program.metadata,
                                        track_number=number) + '.flac'
            logger.debug('ripIfNotRipped: path %r', path)
            trackResult.number = number

            assert isinstance(path, str), "%r is not str" % path
            trackResult.filename = path
            if number > 0:
                trackResult.pregap = self.itable.tracks[number - 1].getPregap()

                trackResult.pre_emphasis = (
                    self.itable.tracks[number - 1].pre_emphasis
                )

            # FIXME: optionally allow overriding reripping
            if os.path.exists(path):
                if path != trackResult.filename:
                    # the path is different (different name/template ?)
                    # but we can copy it
                    logger.debug('previous result %r, expected %r',
                                 trackResult.filename, path)

                logger.info('verifying track %d of %d: %s',
                            number, len(self.itable.tracks),
                            os.path.basename(path))
                if not self.program.verifyTrack(self.runner, trackResult):
                    logger.warning('verification failed, reripping...')
                    os.unlink(path)

            if not os.path.exists(path):
                logger.debug('path %r does not exist, ripping...', path)
                tries = 0
                # we reset durations for test and copy here
                trackResult.testduration = 0.0
                trackResult.copyduration = 0.0
                extra = ""
                while tries < MAX_TRIES:
                    tries += 1
                    if tries > 1:
                        extra = " (try %d)" % tries
                    logger.info('ripping track %d of %d%s: %s',
                                number, len(self.itable.tracks), extra,
                                os.path.basename(path))
                    try:
                        logger.debug('ripIfNotRipped: track %d, try %d',
                                     number, tries)
                        self.program.ripTrack(self.runner, trackResult,
                                              offset=int(self.options.offset),
                                              device=self.device,
                                              taglist=self.program.getTagList(
                                                  number, self.mbdiscid),
                                              overread=self.options.overread,
                                              what='track %d of %d%s' % (
                                                  number,
                                                  len(self.itable.tracks),
                                                  extra))
                        break
                    # FIXME: catching too general exception (Exception)
                    except Exception as e:
                        logger.debug('got exception %r on try %d', e, tries)

                if tries == MAX_TRIES:
                    logger.critical('giving up on track %d after %d times',
                                    number, tries)
                    raise RuntimeError(
                        "track can't be ripped. "
                        "Rip attempts number is equal to 'MAX_TRIES'")
                if trackResult.testcrc == trackResult.copycrc:
                    logger.info('CRCs match for track %d', number)
                else:
                    raise RuntimeError(
                        "CRCs did not match for track %d" % number
                    )

                print('Peak level: %.6f' % (trackResult.peak / 32768.0))
                print('Rip quality: {:.2%}'.format(trackResult.quality))

            # overlay this rip onto the Table
            if number == 0:
                # HTOA goes on index 0 of track 1
                # ignore silence in PREGAP
                if trackResult.peak == SILENT:
                    logger.debug('HTOA peak %r is equal to the SILENT '
                                 'threshold, disregarding', trackResult.peak)
                    self.itable.setFile(1, 0, None,
                                        self.itable.getTrackStart(1), number)
                    logger.debug('unlinking %r', trackResult.filename)
                    os.unlink(trackResult.filename)
                    trackResult.filename = None
                    logger.info('HTOA discarded, contains digital silence')
                else:
                    self.itable.setFile(1, 0, trackResult.filename,
                                        self.itable.getTrackStart(1), number)
            else:
                self.itable.setFile(number, 1, trackResult.filename,
                                    self.itable.getTrackLength(number), number)

            self.program.saveRipResult()

        # check for hidden track one audio
        htoa = self.program.getHTOA()
        if htoa:
            start, stop = htoa
            logger.info('found Hidden Track One Audio from frame %d to %d',
                        start, stop)
            _ripIfNotRipped(0)

        for i, track in enumerate(self.itable.tracks):
            # FIXME: rip data tracks differently
            if not track.audio:
                logger.warning('skipping data track %d, not implemented',
                               i + 1)
                # FIXME: make it work for now
                track.indexes[1].relative = 0
                continue
            _ripIfNotRipped(i + 1)

        logger.debug('writing cue file for %r', discName)
        self.program.writeCue(discName)

        logger.debug('writing m3u file for %r', discName)
        self.program.write_m3u(discName)

        try:
            self.program.verifyImage(self.runner, self.itable)
        except accurip.EntryNotFound:
            logger.warning('AccurateRip entry not found')

        accurip.print_report(self.program.result)

        self.program.saveRipResult()

        self.program.writeLog(discName, self.logger)


class CD(BaseCommand):
    summary = "handle CDs"
    description = "Display and rip CD-DA and metadata."
    device_option = True

    subcommands = {
        'info': Info,
        'rip': Rip
    }
