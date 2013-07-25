import time
import itertools
import operator

from django.core import management

from apps.webdriver_testing.webdriver_base import WebdriverTestCase
from apps.webdriver_testing import data_helpers
from apps.webdriver_testing.data_factories import UserFactory
from apps.webdriver_testing.data_factories import TeamMemberFactory
from apps.webdriver_testing.data_factories import TeamProjectFactory
from apps.webdriver_testing.data_factories import TeamVideoFactory
from apps.webdriver_testing.pages.site_pages import video_page 
from apps.webdriver_testing.pages.site_pages.teams import videos_tab
from apps.webdriver_testing.pages.site_pages import watch_page

class TestCaseVideoResource(WebdriverTestCase):
    """TestSuite for videos via the api

    """
    NEW_BROWSER_PER_TEST_CASE = False

    @classmethod
    def setUpClass(cls):
        super(TestCaseVideoResource, cls).setUpClass()
        cls.user = UserFactory.create(username = 'user')
        cls.data_utils = data_helpers.DataHelpers()
        cls.data_utils.create_user_api_key(cls.user)
        cls.test_video = cls.data_utils.create_video()
        cls.open_team = TeamMemberFactory.create(
            team__name="Cool team",
            team__slug='team-with-projects',
            team__description='this is the coolest, most creative team ever',
            user = cls.user,
            ).team
        cls.project1 = TeamProjectFactory(
            team=cls.open_team,
            name='team project one',
            slug = 'team-project-one',
            description = 'subtitle project number 1',
            guidelines = 'do a good job',
            workflow_enabled=False)
        
        cls.project2 = TeamProjectFactory(
            team=cls.open_team,
            name='team project two',
            workflow_enabled=True)
        cls.video_pg = video_page.VideoPage(cls)
        cls.watch_pg = watch_page.WatchPage(cls)


    def tearDown(self):
        self.browser.get_screenshot_as_file('MYTMP/%s' % self.id())


    def test_video__list(self):
        """List the available videos that are in teams.

        GET /api2/partners/videos/
        video_url 
        team  
        project
        order_by 
            title: ascending
            -title: descending
            created: older videos first
            -created : newer videos
        """
        tv = self.data_utils.create_video()
        TeamVideoFactory.create(team=self.open_team, video=tv, 
                                added_by=self.user)
        for x in range(5):
            TeamVideoFactory.create(team=self.open_team, 
                added_by=self.user,
                project = self.project1)
        url_part = 'videos/'
        status, response = self.data_utils.api_get_request(self.user, url_part)
        video_objects =  response['objects']
        videos_list = []
        for k, v in itertools.groupby(video_objects, 
            operator.itemgetter('id')):
                videos_list.append(k)
        self.assertIn(tv.video_id, videos_list)


    def test_query__project(self):
        """List the available videos.

        GET /api2/partners/videos/
        video_url 
        team  
        project
        order_by 
            title: ascending
            -title: descending
            created: older videos first
            -created : newer videos
        """
        for x in range(5):
            TeamVideoFactory.create(team=self.open_team, 
                added_by=self.user,
                project = self.project1)
        url_part = 'videos/?project=%s' %self.project1.slug
        status, response = self.data_utils.api_get_request(self.user, url_part)
        video_objects =  response['objects']
        videos_list = []
        for k, v in itertools.groupby(video_objects, 
            operator.itemgetter('id')):
                videos_list.append(k)
        self.assertEqual(5, len(videos_list))

    def test_query__team(self):
        """List the available videos.

        GET /api2/partners/videos/team=<team slug>
        """
        for x in range(5):
            TeamVideoFactory.create(team=self.open_team, 
                added_by=self.user,
                project = self.project1)
        url_part = 'videos/?team=%s' %self.open_team.slug
        status, response = self.data_utils.api_get_request(self.user, url_part)
        video_objects =  response['objects']
        videos_list = []
        for k, v in itertools.groupby(video_objects, 
            operator.itemgetter('id')):
                videos_list.append(k)
        self.assertEqual(5, len(videos_list))

    def test_sort__newest(self):
        """List the available videos.

        GET /api2/partners/videos/
        """
        team_vid_list = []
        for x in range(3):
            vid = TeamVideoFactory.create(team=self.open_team, 
                added_by=self.user,
                project = self.project1).video
            team_vid_list.append(vid.title)
            
            time.sleep(1)
        url_part = 'videos/?order_by=-created' 
        status, response = self.data_utils.api_get_request(self.user, url_part)
        video_objects =  response['objects']
        videos_list = []
        for k, v in itertools.groupby(video_objects, 
            operator.itemgetter('title')):
                videos_list.append(k)

        self.assertEqual(team_vid_list[-1], videos_list[0])

    def test_sort__title(self):
        """List the available videos sorted by title (desc)

        GET /api2/partners/videos/
        """
        for x in range(3):
            TeamVideoFactory.create(team=self.open_team, 
                added_by=self.user,
                project = self.project1)
        TeamVideoFactory.create(team=self.open_team, 
                added_by=self.user,
                project = self.project1,
                video__title = 'Zzz-test-video')


        url_part = 'videos/?order_by=-title' 
        status, response = self.data_utils.api_get_request(self.user, url_part)
        
        video_objects =  response['objects']
        videos_list = []
        for k, v in itertools.groupby(video_objects, 
            operator.itemgetter('title')):
                videos_list.append(k)
        self.assertEqual('Zzz-test-video', videos_list[0])

    def test_video__create(self):
        """Add a new video.

        POST /api2/partners/videos/
        """

        url_data = { 'video_url': ('http://qa.pculture.org/amara_tests/'
                                   'Birds_short.webmsd.webm'),
                     'title': 'Test video created via api',
                     'duration': 37 }
        url_part = 'videos/'
        status, response = self.data_utils.post_api_request(self.user, url_part, url_data)
        self.video_pg.open_video_page(response['id'])
        #Check response metadata
        for k, v in url_data.iteritems():
            self.assertEqual(v, response[k])

        #Check video displays on the site
        self.assertTrue(self.video_pg.video_embed_present())


    def test_video__details(self):
        """Get video details.

        GET /api2/partners/videos/[video-id]/
        """

        test_video = self.data_utils.create_video()
        url_part = 'videos/%s/' % test_video.video_id
        s, r = self.data_utils.api_get_request(self.user, url_part)
        self.assertEqual(r['title'], test_video.title)
        self.assertIn(test_video.get_primary_videourl_obj().url, 
                      r['all_urls'])
        self.video_pg.open_page(r['site_url'])
        self.assertEqual(self.video_pg.video_id(), r['id'])


    def test_update__speaker_metatdata(self):
        """Update video metadata, add speaker name field

        PUT /api2/partners/videos/[video-id]/
        """

        tv = self.data_utils.create_video()
        TeamVideoFactory(team=self.open_team, added_by=self.user, video=tv)

        vid_id = tv.video_id 

        url_part = 'videos/%s/' % vid_id
        new_data = {'title': 'MVC webM output sample',
                    'description': ('This is a sample vid converted to webM '
                                   '720p using Miro Video Converter'), 
                    'metadata' : {
                                             'speaker-name': 'Santa',
                                             'location': 'North Pole'
                                         }
                  }
        status, response = self.data_utils.put_api_request(self.user, 
                                                  url_part, new_data)
        self.logger.info(response)
        self.video_pg.open_video_page(vid_id)

        #Check response metadata
        for k, v in new_data.iteritems():
            self.assertEqual(v, response[k])

        #Check video displays on the site
        self.assertIn(new_data['metadata']['speaker-name'], 
            self.video_pg.speaker_name())

#        management.call_command('update_index', interactive=False)

#        test_text = new_data['metadata']['speaker-name']
#        self.watch_pg.open_watch_page()
#        results_pg = self.watch_pg.basic_search(test_text)
#        self.assertTrue(results_pg.search_has_results())
      

 
    def test_update__metatdata(self):
        """Update video metadata, title, description.

        PUT /api2/partners/videos/[video-id]/
        """

        url_data = { 'video_url': ('http://qa.pculture.org/amara_tests/'
                                   'Birds_short.webmsd.webm'),
                     'title': 'Test video created via api',
                     'duration': 37 }
        url_part = 'videos/'
        status, response = self.data_utils.post_api_request(self.user,
                                                   url_part, url_data)
        vid_id = response['id']

        url_part = 'videos/%s/' % vid_id
        new_data = {'title': 'MVC webM output sample',
                    'description': ('This is a sample vid converted to webM '
                                   '720p using Miro Video Converter'), 
                  }
        status, response = self.data_utils.put_api_request(self.user, 
                                                  url_part, new_data)
        self.video_pg.open_video_page(vid_id)

        #Check response metadata
        for k, v in new_data.iteritems():
            self.assertEqual(v, response[k])

        #Check video displays on the site
        self.assertEqual(new_data['description'], 
            self.video_pg.description_text())

    def test_update__team(self):
        """Update the video metadata, add to team and edit video description.

        PUT /api2/partners/videos/[video-id]/
        """

        url_data = { 'video_url': ('http://qa.pculture.org/amara_tests/'
                                   'Birds_short.webmsd.webm'),
                     'title': 'Test video created via api',
                     'duration': 37 }
        url_part = 'videos/'
        status, response = self.data_utils.post_api_request(self.user, 
                                                   url_part, url_data)
        vid_id = response['id']

        url_part = 'videos/%s/' % vid_id
        new_data = {'team': self.open_team.slug,
                    'description': ('This is a sample vid converted to webM '
                                   '720p using Miro Video Converter')
                   }
        status, response = self.data_utils.put_api_request(self.user,
                                                  url_part, new_data)
        self.video_pg.open_video_page(vid_id)

        #Check response metadata
        for k, v in new_data.iteritems():
            self.assertEqual(v, response[k])


        #Check the team is listed on the video page 
        self.assertTrue(self.video_pg.team_slug(self.open_team.slug))

    def test_update__project(self):
        """Edit video, add it to a team and project.

        PUT /api2/partners/videos/[video-id]/
        """
        #Create the initial video via api and get the id
        url_data = { 'video_url': ('http://qa.pculture.org/amara_tests/'
                                   'Birds_short.webmsd.webm'),
                     'title': 'Test video created via api',
                     'duration': 37 }
        url_part = 'videos/'
        s, r = self.data_utils.post_api_request(self.user, 
                                       url_part, url_data)
        vid_id = r['id']

        #Update the video setting the team and project and new description.
        url_part = 'videos/%s/' % vid_id
        new_data = {'team': self.open_team.slug,
                    'project': self.project2.slug,
                    'description': ('This is a sample vid converted to webM '
                                   '720p using Miro Video Converter')
                   }
        status, response = self.data_utils.put_api_request(self.user, 
                                                  url_part, new_data)
        self.video_pg.open_video_page(vid_id)

        #Check response metadata
        for k, v in new_data.iteritems():
            self.assertEqual(v, response[k])

        #Open the projects page on the site and verify video in project.
        team_videos_tab = videos_tab.VideosTab(self)
        team_videos_tab.log_in(self.user.username, 'password')
        team_videos_tab.open_team_project(self.open_team.slug, 
                                          self.project2.slug)
        self.assertTrue(team_videos_tab.video_present(url_data['title']))

