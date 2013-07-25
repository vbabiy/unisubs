// Amara, universalsubtitles.org
//
// Copyright (C) 2013 Participatory Culture Foundation
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as
// published by the Free Software Foundation, either version 3 of the
// License, or (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see
// http://www.gnu.org/licenses/agpl-3.0.html.

var angular = angular || null;

(function() {

    var API_BASE_PATH_TEAMS = '/api2/partners/teams/';
    var API_BASE_PATH_VIDEOS = '/api2/partners/videos/';

    var module = angular.module('amara.SubtitleEditor.subtitles.services', []);

    var getSubtitleFetchAPIUrl = function(videoId, languageCode, versionNumber) {
        var url = API_BASE_PATH_VIDEOS + videoId +
            '/languages/' + languageCode + '/subtitles/' +
            '?format=json&sub_format=dfxp';

        if (versionNumber) {
            url = url + '&version=' + versionNumber;
        }
        return url;
    };
    var getSubtitleSaveAPIUrl = function(videoId, languageCode) {
        var url = API_BASE_PATH_VIDEOS + videoId +
            '/languages/' + languageCode + '/subtitles/';
        return url;
    };
    var getTaskSaveAPIUrl = function(teamSlug, taskID) {
        return API_BASE_PATH_TEAMS + teamSlug + '/tasks/' + taskID + '/';
    };
    var getVideoLangAPIUrl = function(videoId) {
        return API_BASE_PATH_VIDEOS + videoId + '/languages/';
    };

    /*
     * Language object that we return from getLanguage()
     */
    function Language(responseData) {
        /*
         * Create a new Language object
         *
         * responseData is either:
         *   - data that we got back from the API
         *   - or data from the editor_data variable
         *
         * This means that editor_data should be formated exactly as the
         * response data is.
         */
        this.responseData = responseData;
        this.name = responseData.name;
        this.code = responseData.language_code;
        this.versions = responseData.versions;
        if(responseData.is_rtl) {
            this.dir = 'rtl';
        } else {
            this.dir = 'ltr';
        }
        this.isPrimaryAudioLanguage = responseData.is_original;
    }

    module.factory('SubtitleStorage', function($http, EditorData) {

        var authHeaders = EditorData.authHeaders;
        // Map language codes to Language objects
        var languageMap = {};
        _.forEach(EditorData.languages, function(languageData) {
            var language = new Language(languageData);
            languageMap[language.code] = language;
        });

        // Map language_code/version_number to subtitle data
        var cachedSubtitleData = {};
        // Populate cachedSubtitleData with versions from editorData that
        // were pre-filled with the data we need.
        _.each(EditorData.languages, function(language) {
            var language_code = language.language_code;
            cachedSubtitleData[language_code] = {};
            _.each(language.versions, function(version) {
                var versionNum = version.version_no;
                if(version.subtitles) {
                    cachedSubtitleData[language_code][versionNum] = version;
                }
            });
        });


        return {
            approveTask: function(versionNumber, notes) {

                var url = getTaskSaveAPIUrl(EditorData.team_slug,
                        EditorData.task_id);

                var promise = $http({
                    method: 'PUT',
                    url: url,
                    headers: authHeaders,
                    data:  {
                        complete: true,
                        body: notes,
                        version_number: versionNumber,
                    }
                });

                return promise;

            },
            getLanguages: function(callback) {
                return _.values(languageMap);
            },
            getLanguage: function(languageCode) {
                return languageMap[languageCode];
            },
            getSubtitles: function(languageCode, versionNumber, callback){

                // You must supply a language code in order to get subtitles.
                if (!languageCode) {
                    throw Error('You must supply a language code to getSubtitles().');
                }

                var subtitleData;
                var versionNum = parseInt(versionNumber, 10);
                var cacheData = cachedSubtitleData[languageCode][versionNum];
                if(cacheData) {
                   callback(cacheData);
                } else {
                    var url = getSubtitleFetchAPIUrl(EditorData.video.id, languageCode, versionNumber);
                    $http.get(url).success(function(response) {
                        cachedSubtitleData[languageCode][versionNum] = response;
                        callback(response)
                    });
                }
            },
            getPrimaryVideoURL: function() {
                return EditorData.video.primaryVideoURL;
            },
            getVideoURLs: function() {
                return EditorData.video.videoURLs;
            },
            sendBackTask: function(versionNumber, notes) {

                var url = getTaskSaveAPIUrl(EditorData.team_slug,
                        EditorData.task_id);

                var promise = $http({
                    method: 'PUT',
                    url: url,
                    headers: authHeaders,
                    data:  {
                        complete: true,
                        body: notes,
                        send_back: true,
                        version_number: versionNumber,
                    }
                });

                return promise;

            },
            saveSubtitles: function(videoID, languageCode, dfxpString, title,
                                   description, metadata, isComplete) {
                var url = getSubtitleSaveAPIUrl(videoID, languageCode);
                // if isComplete is not specified as true or false, we send
                // null, which means keep the complete flag the same as before
                if(isComplete !== true && isComplete !== false) {
                    isComplete = null;
                }

                var promise = $http({
                    method: 'POST',
                    url: url,
                    headers: authHeaders,
                    data:  {
                        video: videoID,
                        language: languageCode,
                        subtitles: dfxpString,
                        sub_format: 'dfxp',
                        title: title,
                        description: description,
                        metadata: metadata,
                        is_complete: isComplete,
                    }
                });

                return promise;
            }
        };
    });
    module.factory('EditorData', function($window) {
        /**
         * Get the editor data that was passed to us from python
         *
         */
        return $window.editorData;
    });
}).call(this);
