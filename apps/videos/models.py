# Amara, universalsubtitles.org
#
# Copyright (C) 2013 Participatory Culture Foundation
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see
# http://www.gnu.org/licenses/agpl-3.0.html.

import logging
logger = logging.getLogger("videos-models")

import string
import random
from datetime import datetime, date
import time

from django.utils.safestring import mark_safe
from django.core.cache import cache
from django.db import models
from django.db.models.signals import post_save, pre_delete
from django.db.models import Q
from django.db import IntegrityError
from django.utils.dateformat import format as date_format
from django.conf import settings
from django.utils.translation import ugettext_lazy as _
from django.template.defaultfilters import slugify
from django.utils import simplejson as json
from django.core.urlresolvers import reverse


from auth.models import CustomUser as User, Awards
from videos import metadata
from videos.types import video_type_registrar
from videos.feed_parser import FeedParser
from comments.models import Comment
from statistic import st_widget_view_statistic
from statistic.tasks import st_sub_fetch_handler_update, st_video_view_handler_update
from widget import video_cache
from utils.redis_utils import RedisSimpleField
from utils.amazon import S3EnabledImageField
from utils.panslugify import pan_slugify

from apps.teams.moderation_const import MODERATION_STATUSES, UNMODERATED
from raven.contrib.django.models import client
from babelsubs import storage

NO_SUBTITLES, SUBTITLES_FINISHED = range(2)
VIDEO_TYPE_HTML5 = 'H'
VIDEO_TYPE_YOUTUBE = 'Y'
VIDEO_TYPE_BLIPTV = 'B'
VIDEO_TYPE_GOOGLE = 'G'
VIDEO_TYPE_FORA = 'F'
VIDEO_TYPE_USTREAM = 'U'
VIDEO_TYPE_VIMEO = 'V'
VIDEO_TYPE_WISTIA = 'W'
VIDEO_TYPE_DAILYMOTION = 'D'
VIDEO_TYPE_FLV = 'L'
VIDEO_TYPE_BRIGHTCOVE = 'C'
VIDEO_TYPE_MP3 = 'M'
VIDEO_TYPE = (
    (VIDEO_TYPE_HTML5, 'HTML5'),
    (VIDEO_TYPE_YOUTUBE, 'Youtube'),
    (VIDEO_TYPE_BLIPTV, 'Blip.tv'),
    (VIDEO_TYPE_GOOGLE, 'video.google.com'),
    (VIDEO_TYPE_FORA, 'Fora.tv'),
    (VIDEO_TYPE_USTREAM, 'Ustream.tv'),
    (VIDEO_TYPE_VIMEO, 'Vimeo.com'),
    (VIDEO_TYPE_WISTIA, 'Wistia.com'),
    (VIDEO_TYPE_DAILYMOTION, 'dailymotion.com'),
    (VIDEO_TYPE_FLV, 'FLV'),
    (VIDEO_TYPE_BRIGHTCOVE, 'brightcove.com'),
    (VIDEO_TYPE_MP3, 'MP3'),
)
VIDEO_META_CHOICES = (
    (1, 'Author',),
    (2, 'Creation Date',),
)
VIDEO_META_TYPE_NAMES = {}
VIDEO_META_TYPE_VARS = {}
VIDEO_META_TYPE_IDS = {}


def update_metadata_choices():
    """Refresh the VIDEO_META_TYPE_* set of constants.

    When VIDEO_META_CHOICES is updated through VideoMetadata.add_metadata_type()
    the set of extra lookup variables needs to be updated as well.  This
    function does that.

    You should never need to call this directly --
    VideoMetadata.add_metadata_type() will take care of it.

    """
    global VIDEO_META_TYPE_NAMES, VIDEO_META_TYPE_VARS , VIDEO_META_TYPE_IDS
    VIDEO_META_TYPE_NAMES = dict(VIDEO_META_CHOICES)
    VIDEO_META_TYPE_VARS = dict((k, name.lower().replace(' ', '_'))
                                for k, name in VIDEO_META_CHOICES)
    VIDEO_META_TYPE_IDS = dict([choice[::-1] for choice in VIDEO_META_CHOICES])
update_metadata_choices()

WRITELOCK_EXPIRATION = 30 # 30 seconds

ALL_LANGUAGES = [(val, _(name))for val, name in settings.ALL_LANGUAGES]
VALID_LANGUAGE_CODES = [unicode(x[0]) for x in ALL_LANGUAGES]


class AlreadyEditingException(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __unicode__(self):
        return self.msg


# Video
class PublicVideoManager(models.Manager):
    def get_query_set(self):
        return super(PublicVideoManager, self).get_query_set().filter(is_public=True)

class Video(models.Model):
    """Central object in the system"""

    video_id = models.CharField(max_length=255, unique=True)
    title = models.CharField(max_length=2048, blank=True)
    description = models.TextField(blank=True)
    duration = models.PositiveIntegerField(null=True, blank=True, help_text=_(u'in seconds'))
    allow_community_edits = models.BooleanField()
    allow_video_urls_edit = models.BooleanField(default=True)
    writelock_time = models.DateTimeField(null=True, editable=False)
    writelock_session_key = models.CharField(max_length=255, editable=False)
    writelock_owner = models.ForeignKey(User, null=True, editable=False,
                                        related_name="writelock_owners")
    is_subtitled = models.BooleanField(default=False)
    was_subtitled = models.BooleanField(default=False, db_index=True)
    thumbnail = models.CharField(max_length=500, blank=True)
    small_thumbnail = models.CharField(max_length=500, blank=True)
    s3_thumbnail = S3EnabledImageField(
        blank=True,
        upload_to='video/thumbnail/',
        thumb_sizes=(
            (290,165),
            (120,90),))
    edited = models.DateTimeField(null=True, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(User, null=True, blank=True)
    followers = models.ManyToManyField(User, blank=True, related_name='followed_videos', editable=False)
    complete_date = models.DateTimeField(null=True, blank=True, editable=False)
    featured = models.DateTimeField(null=True, blank=True)
    # Metadata fields for this video.  These are translatable strings for
    # metadata on the video.  One example is Speaker name for TED videos.
    #
    # This overlaps with VideoMetadata, but we hopefully will be phasing that
    # out.
    meta_1_type = metadata.MetadataTypeField()
    meta_1_content = metadata.MetadataContentField()
    meta_2_type = metadata.MetadataTypeField()
    meta_2_content = metadata.MetadataContentField()
    meta_3_type = metadata.MetadataTypeField()
    meta_3_content = metadata.MetadataContentField()

    subtitles_fetched_count = models.IntegerField(_(u'Sub.fetched'), default=0, db_index=True, editable=False)
    # counter for evertime the widget plays accounted for both on and off site
    widget_views_count = models.IntegerField(_(u'Widget views'), default=0, db_index=True, editable=False)
    # counter for the # of times the video page is shown in the unisubs website
    view_count = models.PositiveIntegerField(_(u'Views'), default=0, db_index=True, editable=False)

    # Denormalizing the subtitles(had_version) count, in order to get faster joins
    # updated from update_languages_count()
    languages_count = models.PositiveIntegerField(default=0, db_index=True, editable=False)
    moderated_by = models.ForeignKey("teams.Team", blank=True, null=True, related_name="moderating")

    # denormalized convenience from VideoVisibility, should not be set
    # directely
    is_public = models.BooleanField(default=True)

    primary_audio_language_code = models.CharField(max_length=16, blank=True,
                                                   default='',
                                                   choices=ALL_LANGUAGES)

    objects = models.Manager()
    public  = PublicVideoManager()

    def __unicode__(self):
        title = self.title_display()
        if len(title) > 60:
            title = title[:60]+'...'
        return title

    def update_search_index(self):
        """Queue a Celery task that will update this video's Solr entry."""
        from utils.celery_search_index import update_search_index
        update_search_index.delay(self.__class__, self.pk)

    @property
    def views(self):
        """Return a dict of the number of views recorded for this video.

        The map will look like:

            {'month': 100, 'week': 5, 'year': 10223, 'total': 20333}

        Caches this map in memcache for two hours.

        """
        if not hasattr(self, '_video_views_statistic'):
            cache_key = 'video_views_statistic_%s' % self.pk
            views_st = cache.get(cache_key)

            if not views_st:
                views_st = st_widget_view_statistic.get_views(video=self)
                views_st['total'] = self.widget_views_count
                cache.set(cache_key, views_st, 60*60*2)

            self._video_views_statistic = views_st

        return self._video_views_statistic

    def title_display(self, truncate=True):
        v = self.latest_version()

        if v and v.title and v.title.strip():
            title = v.title
        elif self.title and self.title.strip():
            title = self.title
        else:
            try:
                url = self.videourl_set.all()[:1].get().url
                if not url:
                    return 'No title'
            except models.ObjectDoesNotExist:
                return 'No title'

            url = url.strip('/')

            if url.startswith('http://'):
                url = url[7:]

            parts = url.split('/')
            if len(parts) > 1:
                title = '%s/.../%s' % (parts[0], parts[-1])
            else:
                title = url

        if truncate and len(title) > 35:
            title = title[:35] + '...'

        return title

    def title_display_unabridged(self):
        """
        This is just a wrapper around ``title_display`` for use in templates
        """
        return self.title_display(False)

    def update_view_counter(self):
        """Queue a Celery task that will increment the number of views for this video."""
        try:
            st_video_view_handler_update.delay(video_id=self.video_id)
        except:
            client.captureException()

    def update_subtitles_fetched(self, lang=None):
        """Queue a Celery task that will increment the number of times this video's subtitles were fetched."""
        try:
            sl_pk = lang.pk if lang else None
            st_sub_fetch_handler_update.delay(video_id=self.video_id, sl_pk=sl_pk)
            if lang:
                from videos.tasks import update_subtitles_fetched_counter_for_sl

                update_subtitles_fetched_counter_for_sl.delay(sl_pk=lang.pk)
        except:
            client.captureException()

    def get_thumbnail(self, fallback=True):
        """Return a URL to this video's thumbnail.

        This may be an absolute or relative URL, depending on whether the
        thumbnail is stored in our media folder or on S3.

        If fallback is True, it will fallback to the default thumbnail

        """
        if self.s3_thumbnail:
            return self.s3_thumbnail.url

        if self.thumbnail:
            return self.thumbnail
        if fallback:
            return "%simages/video-no-thumbnail-medium.png" % settings.STATIC_URL_BASE

    def get_small_thumbnail(self):
        """Return a URL to a small version of this video's thumbnail, or '' if there isn't one.

        This may be an absolute or relative URL, depending on whether the
        thumbnail is stored in our media folder or on S3.

        """
        if self.s3_thumbnail:
            return self.s3_thumbnail.thumb_url(120, 90)

        if self.small_thumbnail:
            return self.small_thumbnail
        return "%simages/video-no-thumbnail-small.png" % settings.STATIC_URL_BASE

    def get_medium_thumbnail(self):
        """Return a URL to a medium version of this video's thumbnail, or '' if there isn't one.

        This may be an absolute or relative URL, depending on whether the
        thumbnail is stored in our media folder or on S3.

        """
        if self.s3_thumbnail:
            return self.s3_thumbnail.thumb_url(290, 165)

        if self.thumbnail:
            return self.thumbnail

        return "%simages/video-no-thumbnail-medium.png" % settings.STATIC_URL


    def get_team_video(self):
        """Return the TeamVideo object for this video, or None if there isn't one."""
        from teams.models import TeamVideo

        try:
            return self.teamvideo
        except TeamVideo.DoesNotExist:
            return None


    def thumbnail_link(self):
        """Return a URL to this video's thumbnail, or '' if there isn't one.

        Unlike get_thumbnail, this URL will always be absolute.

        """
        if not self.thumbnail:
            return ''

        if self.thumbnail.startswith('http://'):
            return self.thumbnail

        return settings.STATIC_URL+self.thumbnail

    def is_html5(self):
        """Return whether if the original URL for this video is an HTML5 one."""
        try:
            return self.videourl_set.filter(original=True)[:1].get().is_html5()
        except models.ObjectDoesNotExist:
            return False

    def search_page_url(self):
        return self.get_absolute_url()

    def title_for_url(self):
        """Return this video's title with non-URL-friendly characters replaced.

        NOTE: this method is used in videos.search_indexes.VideoSearchResult to
        prevent duplication of code in search result and in DB-query result.

        """
        return pan_slugify(self.title)

    def _get_absolute_url(self, video_id=None):
        """
        NOTE: this method is used in videos.search_indexes.VideoSearchResult
        to prevent duplication of code in search result and in DB-query result

        This is a little hack, because Django uses get_absolute_url in own way,
        so it was impossible just copy to VideoSearchResult
        """
        kwargs = {'video_id': video_id or self.video_id}
        title = self.title_for_url()
        if title:
            kwargs['title'] = title
            return reverse('videos:video_with_title',
                           kwargs=kwargs)
        return reverse('videos:video',  kwargs=kwargs)

    get_absolute_url = _get_absolute_url

    def get_primary_videourl_obj(self):
        """Return the primary video URL for this video if one exists, otherwise None.

        This will return a VideoUrl object.

        """
        try:
            return self.videourl_set.filter(primary=True).all()[:1].get()
        except models.ObjectDoesNotExist:
            return None

    def get_video_url(self):
        """Return the primary video URL for this video if one exists, otherwise None.

        This will return a string of an actual URL, not a VideoUrl.

        """
        vurl = self.get_primary_videourl_obj()
        return vurl.effective_url if vurl else None

    def get_video_urls(self):
        """Return the video URLs for this video."""
        return self.videourl_set.all()

    @classmethod
    def get_or_create_for_url(cls, video_url=None, vt=None, user=None, timestamp=None, fetch_subs_async=True):
        assert video_url or vt, 'should be video URL or VideoType'
        from types.base import VideoTypeError
        from videos.tasks import (
            save_thumbnail_in_s3,
            add_amara_description_credit_to_youtube_video
        )

        try:
            vt = vt or video_type_registrar.video_type_for_url(video_url)
        except VideoTypeError:
            return None, False

        if not vt:
            return None, False

        try:
            video_url_obj = VideoUrl.objects.get(
                url=vt.convert_to_video_url())
            video, created = video_url_obj.video, False
        except models.ObjectDoesNotExist:
            video, created = None, False

        if not video:
            try:
                video_url_obj = VideoUrl.objects.get(
                    type=vt.abbreviation, **vt.create_kwars())
                if user:
                    Action.create_video_handler(video_url_obj.video, user)
                return video_url_obj.video, False
            except VideoUrl.DoesNotExist:
                obj = Video()
                # video types can can fecth subtitles might do it async:
                kwargs = {}
                if vt.CAN_IMPORT_SUBTITLES:
                    kwargs['fetch_subs_async'] = fetch_subs_async
                obj = vt.set_values(obj, **kwargs)
                if obj.title:
                    obj.slug = slugify(obj.title)
                obj.user = user
                obj.save()

                save_thumbnail_in_s3.delay(obj.pk)
                Action.create_video_handler(obj, user)

                #Save video url
                defaults = {
                    'type': vt.abbreviation,
                    'original': True,
                    'primary': True,
                    'added_by': user,
                    'video': obj
                }
                if vt.video_id:
                    defaults['videoid'] = vt.video_id
                video_url_obj, created = VideoUrl.objects.get_or_create(url=vt.convert_to_video_url(),
                                                                        defaults=defaults)
                try:
                    assert video_url_obj.video == obj
                except AssertionError, e:
                    logger.exception(
                        "Data integrity error with video_url_obj ")
                    raise e
                obj.update_search_index()
                video, created = obj, True

        if timestamp and video_url_obj.created != timestamp:
           video_url_obj.created = timestamp
           video_url_obj.save(updates_timestamp=False)
        user and user.notify_by_message and video.followers.add(user)
        if not video_url_obj.owner_username:
            if hasattr(vt, 'username'):
                video_url_obj.owner_username = vt.username
                video_url_obj.save()

        if vt.abbreviation == VIDEO_TYPE_YOUTUBE:
            # Only try to update the Youtube description once we have made sure
            # that we have set the owner_username.
            add_amara_description_credit_to_youtube_video.delay(video.video_id)

        return video, created

    @property
    def language(self):
        """Return the language code of this video's original language as a string.

        Will return None if unknown.

        """
        return self.primary_audio_language_code or None


    @property
    def filename(self):
        """Return a filename-safe version of this video's string representation.

        Could be useful when providing a user with a file related to this video
        to download, etc.

        """
        from django.utils.text import get_valid_filename

        return get_valid_filename(self.title_display(truncate=False))

    def lang_filename(self, language):
        """Return a filename-safe version of this video's string representation with a language code.

        Could be useful when providing a user with a file of subs to download,
        etc.

        """
        name = self.filename
        if not isinstance(language, basestring):
            lang = language.language or u'original'
        else:
            lang = language
        return u'%s.%s' % (name, lang)

    @property
    def subtitle_state(self):
        """Return the subtitling state for this video.

        The value returned will be one of the NO_SUBTITLES or SUBTITLES_FINISHED
        constants.

        """
        return NO_SUBTITLES if self.latest_version() is None else SUBTITLES_FINISHED

    def get_primary_audio_subtitle_language(self):
        """Return the SubtitleLanguage for the primary audio language, or None.

        Caches the result in the object.

        """
        return self._original_subtitle_language()

    def _original_subtitle_language(self):
        """Return the SubtitleLanguage in the original language of this video, or None.

        Caches the result in the object.

        """
        if not hasattr(self, '_original_subtitle'):
            try:
                palc = self.primary_audio_language_code
                original = (self.newsubtitlelanguage_set
                                .filter(language_code=palc)[:1].get())
            except models.ObjectDoesNotExist:
                original = None

            setattr(self, '_original_subtitle', original)

        return getattr(self, '_original_subtitle')

    def has_original_language(self):
        """Return whether this video has a SubtitleLanguage for its original language.

        NOTE: this uses another method which caches the result in the object, so
        this will effectively be cached in-object as well.

        """
        return True if self._original_subtitle_language() else False

    def subtitle_language(self, language_code=None):
        """Return the SubtitleLanguage for this video with the given language code, or None.

        If None is passed as a language_code, the original language
        SubtitleLanguage will be returned.  In this case the value will be
        cached in-object.

        This method can produce surprising results if the video has more
        than one subtitle language with the same code. This is an artifact
        of when we did not allow this. In this case, we return the
        language with the most subtitles.

        """
        try:
            if language_code is None:
                return self._original_subtitle_language()
            else:
                return (self.newsubtitlelanguage_set
                            .filter(language_code=language_code)[:1].get())
        except models.ObjectDoesNotExist:
            return None

    def subtitle_languages(self, language_code):
        """Return all SubtitleLanguages for this video with the given language code."""
        return self.newsubtitlelanguage_set.filter(language_code=language_code)

    def version(self, version_number=None, language=None, public_only=True):
        """Return the SubtitleVersion for this video matching the given criteria.

        If language is given (it must be a SubtitleLanguage, NOT a string
        language code) the version will be looked up for that, otherwise the
        original language will be used.

        If version_no is given, the version with that number will be returned.

        If public_only is True (the default) only versions visible to the public
        (i.e.: not moderated) will be considered.  If it is false all versions
        are eligable.

        If no version fitting all the criteria is found, None is returned.

        """
        if language is None:
            language = self.subtitle_language()

        return (None if language is None else
                language.version(version_number=version_number,
                                 public_only=public_only))

    def latest_version(self, language_code=None, public_only=True):
        """Return the latest SubtitleVersion for this video matching the given criteria.

        If language is given (as a language code string) the version will be
        looked up for that, otherwise the original language will be used.

        If public_only is True (the default) only versions visible to the public
        (i.e.: not moderated) will be considered.  If it is false all versions
        are eligable.

        If no version fitting all the criteria is found, None is returned.

        Deleted versions cannot be retrieved with this method.  If you need
        those you'll need to look them up another way.

        """
        language = self.subtitle_language(language_code)
        return None if language is None else language.get_tip(public=public_only)

    def has_public_version(self):
        """Check if there are any public versions for any language."""
        for language in self.newsubtitlelanguage_set.all():
            if language.has_public_version():
                return True
        return False

    def subtitles(self, version_number=None, language_code=None, language_pk=None):
        if language_pk is None:
            language = self.subtitle_language(language_code)
        else:
            try:
                language = self.newsubtitlelanguage_set.get(pk=language_pk)
            except models.ObjectDoesNotExist:
                language = None

        version = self.version(version_number, language)

        if version:
            return version.get_subtitles()
        else:
            language_code = language.language_code if language else self.primary_audio_language_code
            return storage.SubtitleSet(language_code)

    def latest_subtitles(self, language_code=None, public_only=True):
        version = self.latest_version(language_code, public_only=public_only)
        return [] if version is None else version.get_subtitles()

    def translation_language_codes(self):
        """All iso language codes with finished translations."""
        return set([sl.language for sl
                    in self.newsubtitlelanguage_set.filter(
                    subtitles_complete=True).filter(is_forked=False)])

    @property
    def writelock_owner_name(self):
        """The user who currently has a subtitling writelock on this video."""
        if self.writelock_owner == None:
            return "anonymous"
        else:
            return self.writelock_owner.__unicode__()

    @property
    def is_writelocked(self):
        """Is this video writelocked for subtitling?"""
        if self.writelock_time == None:
            return False
        delta = datetime.now() - self.writelock_time
        seconds = delta.days * 24 * 60 * 60 + delta.seconds
        return seconds < WRITELOCK_EXPIRATION

    def can_writelock(self, request):
        """Can I place a writelock on this video for subtitling?"""
        return self.writelock_session_key == \
            request.browser_id or \
            not self.is_writelocked

    def writelock(self, request):
        """Writelock this video for subtitling."""
        self._make_writelock(request.user, request.browser_id)

    def _make_writelock(self, user, key):
        if user.is_authenticated():
            self.writelock_owner = user
        else:
            self.writelock_owner = None
        self.writelock_session_key = key
        self.writelock_time = datetime.now()

    def release_writelock(self):
        """Writelock this video for subtitling."""
        self.writelock_owner = None
        self.writelock_session_key = ''
        self.writelock_time = None

    def notification_list(self, exclude=None):
        qs = self.followers.filter(notify_by_email=True, is_active=True)
        if exclude:
            if not isinstance(exclude, (list, tuple)):
                exclude = [exclude]
            qs = qs.exclude(pk__in=[u.pk for u in exclude if u and u.is_authenticated()])
        return qs

    def notification_list_all(self, exclude=None):
        users = []
        for language in self.newsubtitlelanguage_set.all():
            for u in language.notification_list(exclude):
                if not u in users:
                    users.append(u)
        for user in self.notification_list(exclude):
            if not user in users:
                users.append(user)
        return users

    def subtitle_language_dict(self):
        langs = {}
        for sl in self.newsubtitlelanguage_set.all():
            if not sl.language:
                continue
            if sl.language in langs:
                langs[sl.language_code].append(sl)
            else:
                langs[sl.language_code] = [sl]
        return langs

    @property
    def is_complete(self):
        """Return whether at least one of this video's languages is marked complete."""

        for sl in self.newsubtitlelanguage_set.all():
            if sl.is_complete_and_synced():
                return True
        return False

    def completed_subtitle_languages(self, public_only=True):
        return [sl for sl in self.newsubtitlelanguage_set.all()
                if sl.is_complete_and_synced(public=public_only)]

    def get_title_display(self):
        """Return a suitable title to display to a user for this video.

        This will use the most specific title if it's present, but if it's blank
        it will fall back to the less-specific-but-at-least-it-exists video
        title instead.

        """
        l = self.subtitle_language()
        return l.get_title() if l else self.title

    def get_description_display(self):
        """Return a suitable description to display to a user for this video.

        This will use the most specific description if it's present, but if it's
        blank it will fall back to the less-specific-but-at-least-it-exists
        video description instead.

        """
        l = self.subtitle_language()
        return l.get_description() if l else self.description


    @property
    def is_moderated(self):
        '''
        Delegates check to team.moderates_video to keep logic in one place.
        This is cached because the widget uses it, and else we'll incurr on
        extra db calls on each widget view.
        '''
        cached_attr_name = '_cache_is_moderated'
        if not hasattr(self, cached_attr_name):
            tv = self.get_team_video()
            setattr(self, cached_attr_name,  tv and tv.team.moderates_videos())
        return cached_attr_name

    def get_metadata(self):
        return metadata.get_metadata_for_video(self)

    def update_metadata(self, new_metadata, commit=True):
        return metadata.update_video(self, new_metadata, commit)

    def metadata(self):
        '''Return a dict of metadata for this video.

        Deprecated: don't use this function in new code.  See comments in
        VideoMetadata for why.

        Example:

        { 'author': 'Sample author',
          'creation_date': datetime(...), }

        '''
        meta = dict([(VIDEO_META_TYPE_VARS[md.key], md.data)
                     for md in self.videometadata_set.all()])

        meta['creation_date'] = VideoMetadata.string_to_date(meta.get('creation_date'))

        return meta

    def can_user_see(self, user):
        team_video = self.get_team_video()

        if not team_video:
            return True

        team = team_video.team

        if team and team.is_visible:
            return True

        return team.is_member(user)

    @property
    def translations(self):
        from subtitles.models import SubtitleLanguage as SL
        return SL.objects.filter(video=self).exclude(
                language_code=self.primary_audio_language_code)

    class Meta(object):
        permissions = (
            ("can_moderate_version"   , "Can moderate version" ,),
        )


def create_video_id(sender, instance, **kwargs):
    """Generate (and set) a random video_id for this video before saving.

    Also fills in the edited timestamp.
    TODO: Split the 'edited' update out into a new function.

    """
    instance.edited = datetime.now()
    if not instance or instance.video_id:
        return
    alphanum = string.letters+string.digits
    instance.video_id = ''.join([random.choice(alphanum) for i in xrange(12)])

def video_delete_handler(sender, instance, **kwargs):
    video_cache.invalidate_cache(instance.video_id)
    # avoid circular dependencies, import here
    from haystack import site
    search_index = site.get_index(Video)
    search_index.backend.remove(instance)


models.signals.pre_save.connect(create_video_id, sender=Video)
models.signals.pre_delete.connect(video_delete_handler, sender=Video)
models.signals.m2m_changed.connect(User.video_followers_change_handler, sender=Video.followers.through)


# VideoMetadata
#
# TODO: remove this this class.  We use this class for a couple things:
#
# Author and Creation Date for team videos.  Why do we need these things?
# Seems like they could easily be attributes on either Video and
# SubtitleVersion.  This probably can go away when we switch to the new
# collaboration model.
#
# Video IDs for partners like TED so we can translate back and forth between
# the our IDs and theirs.  However, it would be better/simpler to just use a
# map table for that.
class VideoMetadata(models.Model):
    video = models.ForeignKey(Video)
    key = models.PositiveIntegerField(choices=VIDEO_META_CHOICES)
    data = models.CharField(max_length=255)

    created = models.DateTimeField(editable=False, auto_now_add=True)
    modified = models.DateTimeField(editable=False, auto_now=True)

    @classmethod
    def add_metadata_type(cls, num, readable_name):
        """Add a new key choice.

        These can't be added at class creation time because some of those types
        live on the integration repo and therefore can't be referenced from
        here.

        This makes sure that if code is trying to do this dynamically we'll
        never allow it to overwrite a key with a different name.

        """
        field = VideoMetadata._meta.get_field_by_name('key')[0]

        choices = field.choices
        for x in choices:
            if x[0] == num and x[1] != readable_name:
                raise ValueError(
                    "Cannot add a metadata value twice, tried %s -> %s which clashes with %s -> %s" %
                    (num, readable_name, x[0], x[1]))
            elif x[0] == num and x[1] == readable_name:
                return
        choices = choices + ((num, readable_name,),)

        # public attr is read only
        global VIDEO_META_CHOICES
        VIDEO_META_CHOICES = field._choices = choices
        update_metadata_choices()


    class Meta:
        ordering = ('created',)
        verbose_name_plural = 'video metadata'

    def __unicode__(self):
        data = self.data
        if len(data) > 30:
            data = data[:30] + '...'
        return u'%s - %s: %s' % (self.video,
                                 self.get_key_display(),
                                 data)

    @classmethod
    def date_to_string(cls, d):
        return d.strftime('%Y-%m-%d') if d else ''

    @classmethod
    def string_to_date(cls, s):
        return datetime.strptime(s, '%Y-%m-%d').date() if s else None


# SubtitleLanguage
class SubtitleLanguage(models.Model):
    video = models.ForeignKey(Video)
    is_original = models.BooleanField()
    language = models.CharField(max_length=16, choices=ALL_LANGUAGES, blank=True)
    writelock_time = models.DateTimeField(null=True, editable=False)
    writelock_session_key = models.CharField(max_length=255, blank=True, editable=False)
    writelock_owner = models.ForeignKey(User, null=True, blank=True, editable=False)
    is_complete = models.BooleanField(default=False)
    subtitle_count = models.IntegerField(default=0, editable=False)

    # has_version: Is there more than one version, and does the latest version
    # have more than 0 subtitles?
    has_version = models.BooleanField(default=False, editable=False,
            db_index=True)

    # had_version: Is there more than one version, and did some previous version
    # have more than 0 subtitles?
    had_version = models.BooleanField(default=False, editable=False)

    is_forked = models.BooleanField(default=False, editable=False)
    created = models.DateTimeField()
    subtitles_fetched_count = models.IntegerField(default=0, editable=False)
    followers = models.ManyToManyField(User, blank=True, related_name='followed_languages', editable=False)
    percent_done = models.IntegerField(default=0, editable=False)
    standard_language = models.ForeignKey('self', null=True, blank=True, editable=False)

    # Fields for the big DMR migration.
    needs_sync = models.BooleanField(default=True, editable=False)
    new_subtitle_language = models.ForeignKey('subtitles.SubtitleLanguage',
                                              related_name='old_subtitle_version',
                                              null=True, blank=True,
                                              editable=False)

    subtitles_fetched_counter = RedisSimpleField()


    def save(self, updates_timestamp=True, *args, **kwargs):
        if 'tern_sync' not in kwargs:
            self.needs_sync = True
        else:
            kwargs.pop('tern_sync')

        if updates_timestamp:
            self.created = datetime.now()
        if self.language:
            assert self.language in VALID_LANGUAGE_CODES, \
                "Subtitle Language %s should be a valid code." % self.language
        super(SubtitleLanguage, self).save(*args, **kwargs)
    @property
    def writelock_owner_name(self):
        if self.writelock_owner == None:
            return "anonymous"
        else:
            return self.writelock_owner.__unicode__()

    @property
    def is_writelocked(self):
        if self.writelock_time == None:
            return False
        delta = datetime.now() - self.writelock_time
        seconds = delta.days * 24 * 60 * 60 + delta.seconds
        return seconds < WRITELOCK_EXPIRATION

    def can_writelock(self, request):
        return self.writelock_session_key == \
               request.browser_id or \
        not self.is_writelocked


    def writelock(self, request):
        if request.user.is_authenticated():
            self.writelock_owner = request.user
        else:
            self.writelock_owner = None
            self.writelock_session_key = request.browser_id
            self.writelock_time = datetime.now()

    def release_writelock(self):
        self.writelock_owner = None
        self.writelock_session_key = ''
        self.writelock_time = None
    class Meta:
        unique_together = (('video', 'language', 'standard_language'),)

    def __unicode__(self):
        if self.is_original and not self.language:
            return 'Original'
        return self.get_language_display()

models.signals.m2m_changed.connect(User.sl_followers_change_handler,
                                   sender=SubtitleLanguage.followers.through)


# SubtitleVersion
class SubtitleVersion(models.Model):
    """
    user -> The legacy data model allowed null users. We do not allow it anymore, but
    for those cases, we've replaced it with the user created on the syncdb commit (see
    apps.auth.CustomUser.get_anonymous.

    """
    language = models.ForeignKey(SubtitleLanguage)
    version_no = models.PositiveIntegerField(default=0)
    datetime_started = models.DateTimeField(editable=False)
    user = models.ForeignKey(User, default=User.get_anonymous)
    note = models.CharField(max_length=512, blank=True)
    time_change = models.FloatField(null=True, blank=True, editable=False)
    text_change = models.FloatField(null=True, blank=True, editable=False)
    notification_sent = models.BooleanField(default=False)
    result_of_rollback = models.BooleanField(default=False)
    forked_from = models.ForeignKey("self", blank=True, null=True)
    is_forked=models.BooleanField(default=False)
    # should not be changed directly, but using teams.moderation. as those will take care
    # of keeping the state constant and also updating metadata when needed
    moderation_status = models.CharField(max_length=32, choices=MODERATION_STATUSES,
                                         default=UNMODERATED, db_index=True)

    title = models.CharField(max_length=2048, blank=True)
    description = models.TextField(blank=True, null=True)

    # Fields for the big DMR migration.
    needs_sync = models.BooleanField(default=True, editable=False)
    new_subtitle_version = models.OneToOneField('subtitles.SubtitleVersion',
                                                related_name='old_subtitle_version',
                                                null=True, blank=True,
                                                editable=False)

    class Meta:
        ordering = ['-version_no']
        unique_together = (('language', 'version_no'),)


    def __unicode__(self):
        return u'%s #%s' % (self.language, self.version_no)


def record_workflow_origin(version, team_video):
    """Figure out and record where the given version started out.

    Should be used right after creation.

    This is a giant ugly hack until we get around to refactoring the subtitle
    adding into a pipeline.  I'm sorry.

    In the future this should go away when we refactor the subtitle pipeline
    out, but until then I couldn't stomach copy/pasting this in three or more
    places.

    """
    if team_video and version and not version.get_workflow_origin():
        tasks = team_video.task_set.incomplete()
        tasks = list(tasks.filter(language=version.subtitle_language.language_code)[:1])

        if tasks:
            open_task_type = tasks[0].get_type_display()

            workflow_origin = {
                'Subtitle': 'transcribe',
                'Translate': 'translate',
                'Review': 'review',
                'Approve': 'approve'
            }.get(open_task_type)

            if workflow_origin:
                version.set_workflow_origin(workflow_origin)

def update_followers(sender, instance, created, **kwargs):
    user = instance.user
    lang = instance.language
    if created and user and user.notify_by_email:
        try:
            lang.followers.add(user)
        except IntegrityError:
            # User already follows the language.
            pass
        try:
            lang.video.followers.add(user)
        except IntegrityError:
            # User already follows the video.
            pass


post_save.connect(Awards.on_subtitle_version_save, SubtitleVersion)
post_save.connect(update_followers, SubtitleVersion)

class SubtitleVersionMetadata(models.Model):
    """This model is used to add extra metadata to SubtitleVersions.

    We could just continually add fields to SubtitleVersion, but that requires
    a new migration each time and bloats the model more and more.  Also, there
    are some pieces of data that are not usually needed, so it makes sense to
    keep them off of the main model.

    """
    KEY_CHOICES = (
        (100, 'reviewed_by'),
        (101, 'approved_by'),
        (200, 'workflow_origin'),
    )
    KEY_NAMES = dict(KEY_CHOICES)
    KEY_IDS = dict([choice[::-1] for choice in KEY_CHOICES])

    WORKFLOW_ORIGINS = ('transcribe', 'translate', 'review', 'approve')

    key = models.PositiveIntegerField(choices=KEY_CHOICES)
    data = models.TextField(blank=True)
    subtitle_version = models.ForeignKey(SubtitleVersion, related_name='metadata')

    created = models.DateTimeField(auto_now_add=True, editable=False)
    modified = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        unique_together = (('key', 'subtitle_version'),)
        verbose_name_plural = 'subtitle version metadata'

    def __unicode__(self):
        return u'%s - %s' % (self.subtitle_version, self.get_key_display())

    def get_data(self):
        if self.get_key_display() in ['reviewed_by', 'approved_by']:
            return User.objects.get(pk=int(self.data))
        else:
            return self.data


# Subtitle
class Subtitle(models.Model):
    version = models.ForeignKey(SubtitleVersion, null=True)
    subtitle_id = models.CharField(max_length=32, blank=True)
    subtitle_order = models.FloatField(null=True)
    subtitle_text = models.CharField(max_length=1024, blank=True)
    # in seconds. if no start time is set, should be null.
    start_time_seconds = models.FloatField(null=True, db_column='start_time')
    # storing both so we don't need to migrate everyone at once
    start_time = models.IntegerField(null=True, default=None, db_column='start_time_ms')
    # in seconds. if no end time is set, should be null.
    end_time_seconds = models.FloatField(null=True, db_column='end_time')
    # storing both so we don't need to migrate everyone at once
    end_time = models.IntegerField(null=True, default=None, db_column='end_time_ms')
    start_of_paragraph = models.BooleanField(default=False)

    class Meta:
        ordering = ['subtitle_order']
        unique_together = (('version', 'subtitle_id'),)


    def __unicode__(self):
        if self.pk:
            return u"(%4s) %s %s -> %s -  = %s -- Version %s" % (
                self.subtitle_order, self.subtitle_id, self.start_time,
                self.end_time,  self.subtitle_text, self.version_id)


# SubtitleMetadata
START_OF_PARAGRAPH = 1

SUBTITLE_META_CHOICES = (
    (START_OF_PARAGRAPH, 'Start of pargraph'),
)


class SubtitleMetadata(models.Model):
    subtitle = models.ForeignKey(Subtitle)
    key = models.PositiveIntegerField(choices=SUBTITLE_META_CHOICES)
    data = models.CharField(max_length=255)

    created = models.DateTimeField(editable=False, auto_now_add=True)
    modified = models.DateTimeField(editable=False, auto_now=True)

    class Meta:
        ordering = ('created',)
        verbose_name_plural = 'subtitles metadata'


# Action
from django.template.loader import render_to_string

class ActionRenderer(object):

    def __init__(self, template_name):
        self.template_name = template_name

    def render(self, item):

        if item.action_type == Action.ADD_VIDEO:
            info = self.render_ADD_VIDEO(item)
        elif item.action_type == Action.CHANGE_TITLE:
            info = self.render_CHANGE_TITLE(item)
        elif item.action_type == Action.COMMENT:
            info = self.render_COMMENT(item)
        elif item.action_type == Action.ADD_VERSION and item.new_language:
            info = self.render_ADD_VERSION(item)
        elif item.action_type == Action.ADD_VIDEO_URL:
            info = self.render_ADD_VIDEO_URL(item)
        elif item.action_type == Action.ADD_TRANSLATION:
            info = self.render_ADD_TRANSLATION(item)
        elif item.action_type == Action.SUBTITLE_REQUEST:
            info = self.render_SUBTITLE_REQUEST(item)
        elif item.action_type == Action.APPROVE_VERSION:
            info = self.render_APPROVE_VERSION(item)
        elif item.action_type == Action.REJECT_VERSION:
            info = self.render_REJECT_VERSION(item)
        elif item.action_type == Action.MEMBER_JOINED:
            info = self.render_MEMBER_JOINED(item)
        elif item.action_type == Action.MEMBER_LEFT:
            info = self.render_MEMBER_LEFT(item)
        elif item.action_type == Action.REVIEW_VERSION:
            info = self.render_REVIEW_VERSION(item)
        elif item.action_type == Action.ACCEPT_VERSION:
            info = self.render_ACCEPT_VERSION(item)
        elif item.action_type == Action.DECLINE_VERSION:
            info = self.render_DECLINE_VERSION(item)
        elif item.action_type == Action.DELETE_VIDEO:
            info = self.render_DELETE_VIDEO(item)
        elif item.action_type == Action.EDIT_URL:
            info = self.render_EDIT_URL(item)
        elif item.action_type == Action.DELETE_URL:
            info = self.render_DELETE_URL(item)
        else:
            info = ''

        context = {
            'info': info,
            'item': item
        }

        return render_to_string(self.template_name, context)

    def _base_kwargs(self, item):
        data = {'language_url': '', 'new_language':''}
        # deleted videos event have no video obj
        if item.video:
            data['video_url']= item.video.get_absolute_url()
            data['video_name'] = unicode(item.video)
        if item.new_language:
            data['new_language'] = item.new_language.get_language_code_display()
            data['language_url'] = item.new_language.get_absolute_url()
        if item.user:
            data["user_url"] = reverse("profiles:profile", kwargs={"user_id":item.user.id})
            data["user"] = item.user
        return data

    def render_REVIEW_VERSION(self, item):
        kwargs = self._base_kwargs(item)
        msg = _('  reviewed <a href="%(language_url)s">%(new_language)s</a> subtitles for <a href="%(video_url)s">%(video_name)s</a>') % kwargs
        return msg

    def render_ACCEPT_VERSION(self, item):
        kwargs = self._base_kwargs(item)
        msg = _('  accepted <a href="%(language_url)s">%(new_language)s</a> subtitles for <a href="%(video_url)s">%(video_name)s</a>') % kwargs
        return msg

    def render_REJECT_VERSION(self, item):
        kwargs = self._base_kwargs(item)
        msg = _('  rejected <a href="%(language_url)s">%(new_language)s</a> subtitles for <a href="%(video_url)s">%(video_name)s</a>') % kwargs
        return msg

    def render_APPROVE_VERSION(self, item):
        kwargs = self._base_kwargs(item)
        msg = _('  approved <a href="%(language_url)s">%(new_language)s</a> subtitles for <a href="%(video_url)s">%(video_name)s</a>') % kwargs
        return msg

    def render_DECLINE_VERSION(self, item):
        kwargs = self._base_kwargs(item)
        msg = _('  declined <a href="%(language_url)s">%(new_language)s</a> subtitles for <a href="%(video_url)s">%(video_name)s</a>') % kwargs
        return msg

    def render_DELETE_VIDEO(self, item):
        kwargs = self._base_kwargs(item)
        kwargs['title'] = item.new_video_title
        msg = _('  deleted a video: "%(title)s"') % kwargs
        return msg

    def render_ADD_VIDEO(self, item):
        if item.user:
            msg = _(u'added <a href="%(video_url)s">&#8220;%(video_name)s&#8221;</a> to Amara')
        else:
            msg = _(u'<a href="%(video_url)s">%(video_name)s</a> added to Amara')

        return msg % self._base_kwargs(item)

    def render_CHANGE_TITLE(self, item):
        if item.user:
            msg = _(u'changed title for <a href="%(video_url)s">%(video_name)s</a>')
        else:
            msg = _(u'Title was changed for <a href="%(video_url)s">%(video_name)s</a>')

        return msg % self._base_kwargs(item)

    def render_COMMENT(self, item):
        kwargs = self._base_kwargs(item)

        if item.new_language:
            kwargs['comments_url'] = '%s#comments' % item.new_language.get_absolute_url()
        else:
            kwargs['comments_url'] = '%s#comments' % kwargs['video_url']

        if item.new_language:
            if item.user:
                msg = _(u'commented on <a href="%(comments_url)s">%(new_language)s subtitles</a> for <a href="%(video_url)s">%(video_name)s</a>')
            else:
                msg = _(u'Comment added for <a href="%(comments_url)s">%(new_language)s subtitles</a> for <a href="%(video_url)s">%(video_name)s</a>')
        else:
            if item.user:
                msg = _(u'commented on <a href="%(video_url)s">%(video_name)s</a>')
            else:
                msg = _(u'Comment added for <a href="%(video_url)s">%(video_name)s</a>')

        return msg % kwargs

    def render_ADD_TRANSLATION(self, item):
        kwargs = self._base_kwargs(item)

        if item.user:
            msg = _(u'started <a href="%(language_url)s">%(new_language)s subtitles</a> for <a href="%(video_url)s">%(video_name)s</a>')
        else:
            msg = _(u'<a href="%(language_url)s">%(new_language)s subtitles</a> started for <a href="%(video_url)s">%(video_name)s</a>')

        return msg % kwargs

    def render_ADD_VERSION(self, item):
        kwargs = self._base_kwargs(item)

        if item.user:
            msg = _(u'edited <a href="%(language_url)s">%(new_language)s subtitles</a> for <a href="%(video_url)s">%(video_name)s</a>')
        else:
            msg = _(u'<a href="%(language_url)s">%(new_language)s subtitles</a> edited for <a href="%(video_url)s">%(video_name)s</a>')

        return msg % kwargs

    def render_ADD_VIDEO_URL(self, item):
        if item.user:
            msg = _(u'added new URL for <a href="%(video_url)s">%(video_name)s</a>')
        else:
            msg = _(u'New URL added for <a href="%(video_url)s">%(video_name)s</a>')

        return msg % self._base_kwargs(item)

    def render_MEMBER_JOINED(self, item):
        msg = _("joined the %(team)s team as a %(role)s" % dict(
             team=item.team, role=item.member.role))
        return msg

    def render_MEMBER_LEFT(self, item):
        msg = _("left the %s team" % (
            item.team))
        return msg

    def render_EDIT_URL(self, item):
        kwargs = self._base_kwargs(item)
        # de-serialize urls from json
        data = {}
        try:
            data = json.loads(item.new_video_title)
        except Exception, e:
            logging.error('Unable to parse urls: {0}'.format(e))
        kwargs['old_url'] = data.get('old_url', 'unknown')
        kwargs['new_url'] = data.get('new_url', 'unknown')
        msg = _('  changed primary url from <a href="%(old_url)s">%(old_url)s</a> to <a href="%(new_url)s">%(new_url)s</a>') % kwargs
        return msg

    def render_DELETE_URL(self, item):
        kwargs = self._base_kwargs(item)
        kwargs['title'] = item.new_video_title
        msg = _('  deleted url <a href="%(title)s">%(title)s</a>') % kwargs
        return msg

class ActionManager(models.Manager):
    def for_team(self, team, public_only=True, ids=False):
        '''Return the actions for the given team.

        If public_only is True, only Actions that should be shown to the general
        public will be returned.

        If ids is True, instead of returning Action objects it will return
        a values_list of their IDs.  This can be useful if you need to work
        around some MySQL brokenness.

        '''
        result = self.filter(
            Q(team=team) |
            Q(video__teamvideo__team=team)
        )

        if public_only:
            result = result.filter(language__has_version=True)

        if ids:
            result = result.values_list('id', flat=True)
        else:
            result = result.select_related(
                'video', 'user', 'language', 'language__video'
            )

        return result

    def for_user(self, user):
        return self.filter(Q(user=user) | Q(team__in=user.teams.all())).distinct()

    def for_user_team_activity(self, user):
        return self.filter(team__in=user.teams.all()).exclude(user=user)

    def for_user_video_activity(self, user):
        return self.filter(video__in=user.videos.all()).exclude(user=user)

    def for_video(self, video, user=None):
        qs = Action.objects.filter(video=video)

        team_video = video.get_team_video()
        if team_video:
            from teams.models import TeamMember

            try:
                user = user if user.is_authenticated() else None
                member = team_video.team.members.get(user=user) if user else None
            except TeamMember.DoesNotExist:
                member = False

            if not member:
                qs = qs.filter(language__has_version=True)

        return qs

class Action(models.Model):
    ADD_VIDEO = 1
    CHANGE_TITLE = 2
    COMMENT = 3
    ADD_VERSION = 4
    ADD_VIDEO_URL = 5
    ADD_TRANSLATION = 6
    SUBTITLE_REQUEST = 7
    APPROVE_VERSION = 8
    MEMBER_JOINED = 9
    REJECT_VERSION = 10
    MEMBER_LEFT = 11
    REVIEW_VERSION = 12
    ACCEPT_VERSION = 13
    DECLINE_VERSION = 14
    DELETE_VIDEO = 15
    EDIT_URL = 16
    DELETE_URL = 17
    TYPES = (
        (ADD_VIDEO, _(u'add video')),
        (CHANGE_TITLE, _(u'change title')),
        (COMMENT, _(u'comment')),
        (ADD_VERSION, _(u'add version')),
        (ADD_TRANSLATION, _(u'add translation')),
        (ADD_VIDEO_URL, _(u'add video url')),
        (SUBTITLE_REQUEST, _(u'request subtitles')),
        (APPROVE_VERSION, _(u'approve version')),
        (MEMBER_JOINED, _(u'add contributor')),
        (MEMBER_LEFT, _(u'remove contributor')),
        (REJECT_VERSION, _(u'reject version')),
        (REVIEW_VERSION, _(u'review version')),
        (ACCEPT_VERSION, _(u'accept version')),
        (DECLINE_VERSION, _(u'decline version')),
        (DELETE_VIDEO, _(u'delete video')),
        (EDIT_URL, _(u'edit url')),
        (DELETE_URL, _(u'delete url')),
    )

    renderer = ActionRenderer('videos/_action_tpl.html')
    renderer_for_video = ActionRenderer('videos/_action_tpl_video.html')

    user = models.ForeignKey(User, null=True, blank=True)
    video = models.ForeignKey(Video, null=True, blank=True)
    language = models.ForeignKey(SubtitleLanguage, blank=True, null=True)
    new_language = models.ForeignKey('subtitles.SubtitleLanguage', blank=True, null=True)
    team = models.ForeignKey("teams.Team", blank=True, null=True)
    member = models.ForeignKey("teams.TeamMember", blank=True, null=True)
    comment = models.ForeignKey(Comment, blank=True, null=True)
    action_type = models.IntegerField(choices=TYPES)
    # we also store the video's title for deleted videos
    new_video_title = models.CharField(max_length=2048, blank=True)
    created = models.DateTimeField()

    objects = ActionManager()

    class Meta:
        ordering = ['-id']
        get_latest_by = 'id'

    def __unicode__(self):
        u = self.user and self.user.__unicode__() or 'Anonymous'
        return u'%s: %s(%s)' % (u, self.get_action_type_display(), self.created)

    def render(self, renderer=None):
        if not renderer:
            renderer = self.renderer

        return renderer.render(self)

    def render_for_video(self):
        return self.render(self.renderer_for_video)

    def is_add_video_url(self):
        return self.action_type == self.ADD_VIDEO_URL

    def is_add_version(self):
        return self.action_type == self.ADD_VERSION

    def is_member_joined(self):
        return self.action_type == self.MEMBER_JOINED

    def is_comment(self):
        return self.action_type == self.COMMENT

    def is_change_title(self):
        return self.action_type == self.CHANGE_TITLE

    def is_add_video(self):
        return self.action_type == self.ADD_VIDEO

    def type(self):
        if self.comment_id:
            return 'commented'
        return 'edited'

    def time(self):
        if self.created.date() == date.today():
            format = 'g:i A'
        else:
            format = 'g:i A, j M Y'
        return date_format(self.created, format)

    def uprofile(self):
        try:
            return self.user.profile_set.all()[0]
        except IndexError:
            pass

    @classmethod
    def create_member_left_handler(cls, team, user):
        action = cls(team=team, user=user)
        action.created = datetime.now()
        action.action_type = cls.MEMBER_LEFT
        action.save()

    @classmethod
    def create_new_member_handler(cls, member):
        action = cls(team=member.team, user=member.user)
        action.created = datetime.now()
        action.action_type = cls.MEMBER_JOINED
        action.member = member
        action.save()

    @classmethod
    def change_title_handler(cls, video, user):
        action = cls(new_video_title=video.title, video=video)
        action.user = user.is_authenticated() and user or None
        action.created = datetime.now()
        action.action_type = cls.CHANGE_TITLE
        action.save()

    @classmethod
    def create_comment_handler(cls, sender, instance, created, **kwargs):
        if created:
            from subtitles.models import SubtitleLanguage as NewSubtitleLanguage
            model_class = instance.content_type.model_class()
            obj = cls(user=instance.user)
            obj.comment = instance
            obj.created = instance.submit_date
            obj.action_type = cls.COMMENT
            if issubclass(model_class, Video):
                obj.video_id = instance.object_pk
            if issubclass(model_class, NewSubtitleLanguage):
                obj.new_language_id = instance.object_pk
                obj.video = instance.content_object.video
            obj.save()

    @classmethod
    def create_caption_handler(cls, instance, timestamp):
        user = instance.author
        video = instance.video
        language = instance.subtitle_language

        obj = cls(user=user, video=video, new_language=language)

        if instance.version_number == 0:
            obj.action_type = cls.ADD_TRANSLATION
        else:
            obj.action_type = cls.ADD_VERSION

        obj.created = timestamp
        obj.save()

    @classmethod
    def create_video_handler(cls, video, user=None):
        obj = cls(video=video)
        obj.action_type = cls.ADD_VIDEO
        obj.user = user
        obj.created = video.created or datetime.now()
        obj.save()

    @classmethod
    def delete_video_handler(cls, video, team, user=None):
        action = cls(team=team)
        action.new_video_title = video.get_title_display()
        action.action_type = cls.DELETE_VIDEO
        action.user = user
        action.created = datetime.now()
        action.save()

    @classmethod
    def create_video_url_handler(cls, sender, instance, created, **kwargs):
        if created and instance.video_id and sender.objects.filter(video=instance.video).count() > 1:
            obj = cls(video=instance.video)
            obj.user = instance.added_by
            obj.action_type = cls.ADD_VIDEO_URL
            obj.created = instance.created
            obj.save()

    @classmethod
    def create_approved_video_handler(cls, version, moderator,  **kwargs):
        obj = cls(video=version.video)
        obj.new_language = version.subtitle_language
        obj.user = moderator
        obj.action_type = cls.APPROVE_VERSION
        obj.created = kwargs.get('datetime_started' , datetime.now())
        obj.save()

    @classmethod
    def create_rejected_video_handler(cls, version, moderator,  **kwargs):
        obj = cls(video=version.video)
        obj.new_language = version.subtitle_language
        obj.user = moderator
        obj.action_type = cls.REJECT_VERSION
        obj.created = datetime.now()
        obj.save()

    @classmethod
    def create_reviewed_video_handler(cls, version, moderator,  **kwargs):
        obj = cls(video=version.video)
        obj.new_language = version.language
        obj.user = moderator
        obj.action_type = cls.REVIEW_VERSION
        obj.created = datetime.now()
        obj.save()

    @classmethod
    def create_accepted_video_handler(cls, version, moderator,  **kwargs):
        obj = cls(video=version.video)
        obj.new_language = version.subtitle_language
        obj.user = moderator
        obj.action_type = cls.ACCEPT_VERSION
        obj.created = datetime.now()
        obj.save()

    @classmethod
    def create_declined_video_handler(cls, version, moderator,  **kwargs):
        obj = cls(video=version.video)
        obj.new_language = version.subtitle_language
        obj.user = moderator
        obj.action_type = cls.DECLINE_VERSION
        obj.created = datetime.now()
        obj.save()


    @classmethod
    def create_subrequest_handler(cls, sender, instance, created, **kwargs):
        if created:
            obj = cls.objects.create(
                user=instance.user,
                video=instance.video,
                action_type=cls.SUBTITLE_REQUEST,
                created=datetime.now()
            )

            instance.action = obj
            instance.save()


post_save.connect(Action.create_comment_handler, Comment)


# UserTestResult
class UserTestResult(models.Model):
    email = models.EmailField()
    browser = models.CharField(max_length=1024)
    task1 = models.TextField()
    task2 = models.TextField(blank=True)
    task3 = models.TextField(blank=True)
    get_updates = models.BooleanField(default=False)


# VideoUrl
class VideoUrl(models.Model):
    video = models.ForeignKey(Video)
    type = models.CharField(max_length=1, choices=VIDEO_TYPE)
    url = models.URLField(max_length=255, unique=True)
    videoid = models.CharField(max_length=50, blank=True)
    primary = models.BooleanField(default=False)
    original = models.BooleanField(default=False)
    created = models.DateTimeField()
    added_by = models.ForeignKey(User, null=True, blank=True)
    # this is the owner if the video is from a third party website
    # shuch as Youtube or Vimeo username
    owner_username = models.CharField(max_length=255, blank=True, null=True)


    class Meta:
        ordering = ("video", "-primary",)

    def __unicode__(self):
        return self.url

    def is_html5(self):
        return self.type == VIDEO_TYPE_HTML5

    @models.permalink
    def get_absolute_url(self):

        return ('videos:video_url', [self.video.video_id, self.pk])

    def unique_error_message(self, model_class, unique_check):
        if unique_check[0] == 'url':
            vu_obj = VideoUrl.objects.get(url=self.url)
            return mark_safe(_('This URL already <a href="%(url)s">exists</a> as its own video in our system. You can\'t add it as a secondary URL.') % {'url': vu_obj.get_absolute_url()})
        return super(VideoUrl, self).unique_error_message(model_class, unique_check)

    def created_as_time(self):
        #for sorting in js
        return time.mktime(self.created.timetuple())

    def make_primary(self, user=None):
        # create activity item
        obj = Action(video=self.video)
        urls = VideoUrl.objects.filter(video=self.video)
        obj.action_type = Action.EDIT_URL
        data = {
            'old_url': urls.filter(primary=True)[0].url,
            'new_url': self.url,
        }
        obj.new_video_title = json.dumps(data)
        obj.created = datetime.now()
        obj.user = user
        obj.save()
        # reset existing urls to non-primary
        VideoUrl.objects.filter(video=self.video).exclude(pk=self.pk).update(
            primary=False)
        # set this one to primary
        self.primary = True
        self.save(updates_timestamp=False)

    @property
    def effective_url(self):
        try:
            return video_type_registrar[self.type].video_url(self)
        except KeyError:
            vt = video_type_registrar.video_type_for_url(self.url)
            self.type = vt.abbreviation
            self.save()
            return video_type_registrar[self.type].video_url(self)

    def save(self, updates_timestamp=True, *args, **kwargs):
        assert self.type != '', "Can't set an empty type"
        if updates_timestamp:
            self.created = datetime.now()
        super(VideoUrl, self).save(*args, **kwargs)

def video_url_remove_handler(sender, instance, **kwargs):
    print('Invalidating cache')
    video_cache.invalidate_cache(instance.video.video_id)


models.signals.pre_save.connect(create_video_id, sender=Video)
models.signals.pre_delete.connect(video_delete_handler, sender=Video)
post_save.connect(Action.create_video_url_handler, VideoUrl)
post_save.connect(video_cache.on_video_url_save, VideoUrl)
pre_delete.connect(video_cache.on_video_url_delete, VideoUrl)


# VideoFeed
class VideoFeed(models.Model):
    url = models.URLField()
    last_link = models.URLField(blank=True)
    created = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(User, blank=True, null=True)

    YOUTUBE_PAGE_SIZE = 25

    def __unicode__(self):
        return self.url

    def update(self):
        feed_parser = FeedParser(self.url)

        checked_entries = 0
        last_link = self.last_link

        try:
            self.last_link = feed_parser.feed.entries[0]['link']
            self.save()
        except (IndexError, KeyError):
            pass

        checked_entries += self._create_videos(feed_parser, last_link)

        if not last_link and 'youtube' in self.url:
            next_url = [x for x in feed_parser.feed.feed.get('links', []) if x['rel'] == 'next']

            while next_url:
                url = next_url[0].href
                feed_parser = FeedParser(url)
                checked_entries += self._create_videos(feed_parser, last_link)
                next_url = [x for x in feed_parser.feed.feed.get('links', []) if x['rel'] == 'next']

        return checked_entries

    def _get_pages(self, total):
        pages = float(total) / float(self.YOUTUBE_PAGE_SIZE)

        if pages > int(pages):
            pages = int(pages) + 1
        else:
            pages = int(pages)

        return pages

    def _create_videos(self, feed_parser, last_link):
        checked_entries = 0

        _iter = feed_parser.items(since=last_link, ignore_error=True)

        for vt, info, entry in _iter:
            vt and Video.get_or_create_for_url(vt=vt, user=self.user)
            checked_entries += 1

        return checked_entries
