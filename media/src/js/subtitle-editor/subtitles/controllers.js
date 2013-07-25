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

    var module = angular.module('amara.SubtitleEditor.subtitles.controllers', []);

    module.controller('LanguageSelectorController', function($scope) {
        /**
         * This controller is responsible for the language and version selector
         * widget.  The widget allows the user to select a reference language and
         * version when translating subtitles into another language.
         *
         * The $scope contains 'languages' and 'versions'.  Each list is
         * represented in the UI as a <select> element.  The selected language or
         * version is stored on the $scope as a 'language' and 'version' model.
         *
         * Whenever the language selection is changed, we update the list of
         * versions.  When a new version is selected, we retrieve the subtitles
         * (either from memory or from the API via ajax) and display them in the
         * side panel.
         */

        /**
         * The second param will be either an int or the version data object,
         * depending if this is being set from the bootstrapped value or later
         * fetches.
         */
        $scope.versionNumber = null;
        $scope.currentVersion = null;
        $scope.versions = [];
        $scope.languageChanged = function() {
            if (!$scope.language) {
                return;
            }

            $scope.versions = _.sortBy($scope.language.versions, function(item) {
                return item.version_no;
            }).reverse();

            $scope.versionNumber = getLastPublicVersion();
            if($scope.versionNumber !== null) {
                loadSubtitles();
            } else {
                loadEmptySubtitles();
            }
        };

        function getLastPublicVersion() {
            for(var i=0; i < $scope.versions.length; i++) {
                if($scope.versions[i].visibility == 'Public') {
                    return $scope.versions[i].version_no.toString();
                }
            }
            return null;
        }
        $scope.findVersion = function(versionNumber) {
            for(var i = 0; i < $scope.versions.length; i++) {
                if($scope.versions[i].version_no == versionNumber) {
                    return $scope.versions[i];
                }
            }
            return null;
        }
        $scope.versionNumberChanged = function() {
            loadSubtitles();
        };

        function loadSubtitles() {
            /* loadSubtitles gets called a bunch of times as we are populating
             * the dropdowns.  Wrap it in a $evalAsync so that we wait for things
             * to settle before actually trying to load them.
             */
            $scope.$evalAsync(function() {
                var newVersion = $scope.findVersion($scope.versionNumber);
                if(!newVersion || newVersion == $scope.currentVersion) {
                    return;
                }

                if(newVersion.visibility == 'Public') {
                    $scope.currentVersion = newVersion;
                    $scope.referenceSubtitles.getSubtitles(
                        $scope.language.code, newVersion.version_no);
                } else {
                    loadEmptySubtitles();
                }
            });
        }

        function loadEmptySubtitles() {
            $scope.currentVersion = null;
            $scope.referenceSubtitles.initEmptySubtitles(
                    $scope.language.code);
        }

        function pickInitialLanguage() {
            if(!$scope.languages) {
                return null;
            }
            // try to pick the primary audio language
            for(var i = 0; i < $scope.languages.length; i++) {
                var language = $scope.languages[i];
                if(language.isPrimaryAudioLanguage) {
                    return language;
                }
            }
            // fall back on picking the first language
            return $scope.languages[0];
        }

        $scope.setInitialDisplayLanguage = function(allLanguages) {
            $scope.languages = allLanguages;
            $scope.language = pickInitialLanguage();
            $scope.languageChanged();
        }

        $scope.$watch('language', function(newValue, oldValue) {
            $scope.languageChanged();
        });
        $scope.$watch('versionNumber', $scope.versionNumberChanged);
    });

    module.controller('SaveSessionController', function($scope, $q, SubtitleStorage, EditorData) {

        $scope.changesMade = false;
        $scope.nextVersionNumber = null;
        $scope.fromOldEditor = Boolean(EditorData.oldEditorURL);
        $scope.primaryVideoURL = '/videos/' + $scope.videoId + '/';

        if ($scope.fromOldEditor) {
            $scope.dialogURL = EditorData.oldEditorURL;
        }

        $scope.discard = function() {
            $scope.showCloseModal(false);
        };
        $scope.getNotes = function() {
            var collabScope = angular.element($('section.collab').get(0)).scope();
            return collabScope.notes || '';
        };
        $scope.saveAndApprove = function() {
            if($scope.changesMade) {
                var message = 'Subtitles saved, task approved. Redirecting…';
            } else {
                var message = 'Task approved. Redirecting…';
            }

            $scope.saveSession(true).then(function(versionNumber) {
                if ($scope.status === 'saved') {

                    $scope.status = 'approving';

                    SubtitleStorage.approveTask(versionNumber, $scope.getNotes()).then(function onSuccess(response) {

                        $scope.$root.$emit('show-loading-modal', message);
                        window.location = $scope.primaryVideoURL;

                    }, function onError() {
                        $scope.status = 'error';
                        $scope.showErrorModal();
                    });
                }
            });

        };
        $scope.save = function(options) {
            var defaults = {
                force: false,
                markComplete: null,
                allowResume: true,
            }
            if(options !== undefined) {
                angular.extend(defaults, options);
            }
            options = defaults;

            if(!$scope.changesMade && !options.markComplete &&!options.force) {
                return;
            }

            $scope.saveSession(options.markComplete)
                .then(function(versionNumber) {
                $scope.nextVersionNumber = versionNumber;
                $scope.showCloseModal(options.allowResume);
            });
        };
        $scope.saveAndSendBack = function() {
            if($scope.changesMade) {
                var message = 'Subtitles saved, task sent back. Redirecting…';
            } else {
                var message = 'Task sent back. Redirecting…';
            }
            $scope.saveSession().then(function(versionNumber) {
                if ($scope.status === 'saved') {

                    $scope.status = 'sending-back';

                    SubtitleStorage.sendBackTask(versionNumber, $scope.getNotes()).then(function onSuccess(response) {

                        $scope.$root.$emit('show-loading-modal', message);
                        window.location = $scope.primaryVideoURL;
                        
                    }, function onError() {
                        $scope.status = 'error';
                        $scope.showErrorModal();
                    });

                }
            });
        };
        $scope.saveSession = function(markComplete) {
            // Save the current session
            //
            // Returns a promise that will be resolved with the version number
            // of the new version when the save is complete.  If nothing has
            // changed, then we don't save anything and return the current
            // version number.
            if ($scope.status !== 'saving') {
                var deferred = $q.defer();

                if($scope.changesMade || markComplete !== undefined) {
                    // changes have been made, we need to save the subtitles
                    $scope.status = 'saving';
                    var promise = $scope.saveSubtitles(markComplete);
                    promise.then(function onSuccess(response) {
                        $scope.status = 'saved';
                        $scope.changesMade = false;
                        deferred.resolve(response.data.version_number);
                    }, function onError(e) {
                        $scope.status = 'error';
                        $scope.showErrorModal();
                        deferred.reject(e);
                    });
                } else {
                    // no changes made, just return the current version
                    $scope.status = 'saved';
                    deferred.resolve($scope.workingSubtitles.versionNumber);
                }

                return deferred.promise;
            }
        };
        function resumeEditing() {
            $scope.status = '';
            $scope.workingSubtitles.versionNumber = $scope.nextVersionNumber;
            $scope.nextVersionNumber = null;
        }
        $scope.showCloseModal = function(allowResume) {
            var buttons = [];

            if (allowResume && $scope.nextVersionNumber)  {
                buttons.push({
                    text: 'Resume editing',
                    class: 'yes',
                    fn: function() {
                        $scope.$root.$emit('hide-modal');
                        resumeEditing();
                    },
                });
            }

            if ($scope.fromOldEditor) {
                buttons.push({
                    'text': 'Back to full editor', 'class': 'yes', 'fn': function() {
                        window.location = $scope.dialogURL;
                    }
                });
            }

            buttons.push({
                'text': 'Exit', 'class': 'no', 'fn': function() {
                    window.location = $scope.primaryVideoURL;
                }
            });

            if ($scope.status !== 'saved') {

                buttons.push({
                    'text': "Wait, don't discard my changes!", 'class': 'last-chance', 'fn': function() {
                        $scope.$root.$emit('hide-modal');
                    }
                });

            }

            if($scope.status === 'saved') {
                if(allowResume) {
                    var heading = "You've saved a new revision!";
                } else {
                    var heading = "Subtitles saved";
                }
            } else if($scope.changesMade) {
                var heading = 'Your changes will be discarded.';
            } else {
                var heading = 'You are leaving the editor';
            }

            $scope.$root.$emit('show-modal', {
                heading: heading,
                buttons: buttons
            });
        };
        $scope.showErrorModal = function(message) {
            function onClose() {
                // set changesMade to false to prevent the beforeunload code
                // from double-checking with the user to close the page.
                $scope.changesMade = false;
                window.location = '/videos/' + $scope.videoId + "/";
            }

            $scope.$root.$emit("show-modal", {
                heading: message || "There was an error saving your subtitles. You'll need to copy and save your subtitles below, and upload them to the system later.",
                buttons: [
                    {'text': 'Close editor', 'class': 'no', 'fn': onClose}
                ]
            });
            $scope.$root.$emit('show-modal-download');
        };

        $scope.$root.$on('approve-task', function() {
            $scope.saveAndApprove();
        });
        $scope.$root.$on('save', function(evt, options) {
            $scope.save(options);
        });
        $scope.$root.$on('send-back-task', function() {
            $scope.saveAndSendBack();
        });
        $scope.$root.$on('work-done', function() {
            $scope.changesMade = true;
        });

        window.onbeforeunload = function() {
            if($scope.changesMade) {
              return "You have unsaved work";
            } else {
              return null;
            }
        };
    });

    module.controller('WorkingSubtitlesController', function($scope, DomWindow) {
        /**
         * Handles the subtitles the user is working on.
         */
        var willSync = {start: null, end:null};
        var subtitleList = $scope.workingSubtitles.subtitleList;

        function updateSyncHelpers() {
            var startIndex = null, endIndex = null;
            if(willSync.start !== null) {
                startIndex = subtitleList.getIndex(willSync.start);
            }
            if(willSync.end !== null) {
                endIndex = subtitleList.getIndex(willSync.end);
            }
            $scope.positionSyncHelpers(startIndex, endIndex);
        }

        $scope.$root.$on('will-sync-changed', function(evt, newWillSync) {
            willSync = newWillSync;
            updateSyncHelpers();
        });

        subtitleList.addChangeCallback(function(change) {
            if(change == 'insert' || change == 'remove') {
                updateSyncHelpers();
            }
        });

        $scope.$watch('currentEdit.inProgress()', function(value) {
            if(value) {
                trackMouseDown();
            } else {
                DomWindow.offDocumentEvent('mousedown.subtitle-edit');
            }

        });

        function trackMouseDown() {
            // Disconnect any previous handlers to keep sanity
            DomWindow.offDocumentEvent('mousedown.subtitle-edit');
            DomWindow.onDocumentEvent('mousedown.subtitle-edit', function(evt) {
                var subtitle = $scope.currentEdit.draft.storedSubtitle;
                var li = $scope.getSubtitleRepeatItem(subtitle);
                var clicked = $(evt.target);
                var textarea = $('textarea.subtitle-edit', li);
                if(clicked[0] != textarea[0] &&
                    !clicked.hasClass('info-tray') &&
                    clicked.parents('.info-tray').length == 0) {
                    $scope.$apply(function() {
                        finishEdit(true);
                    });
                }
            });
        }

        function finishEdit(commitChanges) {
            // Tell the root scope that we're no longer editing, now.
            if($scope.currentEdit.finish(commitChanges, subtitleList)) {
                $scope.$root.$emit('work-done');
            }
        };

        function insertAndStartEdit(before) {
            var newSub = subtitleList.insertSubtitleBefore(before);
            $scope.currentEdit.start(newSub);
        }

        $scope.onSubtitleClick = function(evt, subtitle, action) {
            var madeChange = false;
            switch(action) {
                case 'insert':
                    insertAndStartEdit(subtitle);
                    madeChange = true;
                    break;

                case 'remove':
                    if($scope.currentEdit.isForSubtitle(subtitle)) {
                        $scope.currentEdit.finish(false);
                    }
                    subtitleList.removeSubtitle(subtitle);
                    madeChange = true;
                    break;

                case 'edit':
                    if(!$scope.currentEdit.isForSubtitle(subtitle)) {
                        var caret = DomWindow.caretPos();
                        $scope.currentEdit.start(subtitle);
                        $scope.currentEdit.draft.initialCaretPos = caret;
                        madeChange = true;
                    }
                    break;
            }
            if(madeChange) {
                evt.preventDefault();
                $scope.$root.$emit('work-done');
            }
        }

        $scope.newSubtitleClicked = function(evt) {
            insertAndStartEdit(null);
            evt.preventDefault();
            $scope.$root.$emit('work-done');
        }

        $scope.onEditKeydown = function(evt) {
            var subtitle = $scope.currentEdit.draft.storedSubtitle;

            if (evt.keyCode === 13 && !evt.shiftKey) {
                // Enter without shift finishes editing
                var nextSubtitle = subtitleList.nextSubtitle(subtitle);
                finishEdit(true);
                if(nextSubtitle === null) {
                    if(!$scope.timelineShown) {
                        insertAndStartEdit(null);
                    }
                } else {
                    $scope.currentEdit.start(nextSubtitle);
                    $scope.$root.$emit('scroll-to-subtitle', nextSubtitle);
                }
                evt.preventDefault();
            } else if (evt.keyCode === 27) {
                // Escape cancels editing
                finishEdit(false);
                if(subtitle.markdown == '') {
                    subtitleList.removeSubtitle(subtitle);
                }
                evt.preventDefault();
            } else if (evt.keyCode == 9) {
                // Tab navigates to other subs
                finishEdit(true);
                if(!evt.shiftKey) {
                    var targetSub = subtitleList.nextSubtitle(subtitle);
                } else {
                    var targetSub = subtitleList.prevSubtitle(subtitle);
                }
                if(targetSub !== null) {
                    $scope.currentEdit.start(targetSub);
                    $scope.$root.$emit('scroll-to-subtitle', targetSub);
                }
                evt.preventDefault();
            }
        }
        
        $scope.bottomState = function() {
            if($scope.currentEdit.inProgress()) {
                return 'edit-help'
            } else if($scope.timelineShown) {
                return 'add-button'
            } else {
                return 'type-shortcuts-help'
            }
        }
    });

    module.controller("SubtitleMetadataController", function($scope) {
        // We should expand the section by default if we are not working with
        // the original language.  That way translators will know that they
        // should fill something out there.
        var language = $scope.workingSubtitles.language;
        $scope.collapsed = language.isPrimaryAudioLanguage;
        $scope.onExpanderClicked = function($event) {
            $scope.$root.$emit('metadata-expander-clicked');
            $event.preventDefault();
        }
        $scope.$root.$on('metadata-expander-clicked', function() {
            $scope.collapsed = !$scope.collapsed;
        });
    });
}).call(this);
