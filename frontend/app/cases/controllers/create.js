angular.module('app.cases').config(caseConfig);

caseConfig.$inject = ['$stateProvider'];
function caseConfig($stateProvider) {
    $stateProvider.state('base.cases.create', {
        url: '/create',
        views: {
            '@': {
                templateUrl: '/cases/create',
                controller: CaseCreateController
            }
        },
        ncyBreadcrumb: {
            label: 'Create'
        }
    });
    $stateProvider.state('base.cases.create.fromContact', {
        url: '/contact/{id:[0-9]{1,}}',
        views: {
            '@': {
                templateUrl: function(elem, attr) {
                    return '/cases/create/from_contact/' + elem.id +'/';
                },
                controller: CaseCreateController
            }
        },
        ncyBreadcrumb: {
            skip: true
        }
    });
    $stateProvider.state('base.cases.create.fromAccount', {
        url: '/account/{id:[0-9]{1,}}',
        views: {
            '@': {
                templateUrl: function(elem, attr) {
                    return '/cases/create/from_account/' + elem.id +'/';
                },
                controller: CaseCreateController
            }
        },
        ncyBreadcrumb: {
            skip: true
        }
    });
}

angular.module('app.cases').controller('CaseCreateController', CaseCreateController);

CaseCreateController.$inject = ['$scope', 'UserTeams'];
function CaseCreateController($scope, UserTeams) {
    $scope.conf.pageTitleBig = 'New case';
    $scope.conf.pageTitleSmall = 'making cases';

    activate();

    ////

    function activate() {
        HLCases.addAssignToMeButton();
        HLSelect2.init();
        _setupSubmitListener();
        _setupChangeAssigned();
    }

    function _setupChangeAssigned() {
        // Add the teams of the assigned_to user.
        UserTeams.query().$promise.then(function(teams) {
            $('#id_assigned_to').change(function(changedUser) {
                for (var i = 0; i < teams.length; i++) {
                    var team = teams[i];
                    for (var u = 0; u < team.user_set.length; u++) {
                        var user = team.user_set[u];
                        if (user.id.toString() === changedUser.val) {
                            var selected = $('#id_assigned_to_groups').select2('val');
                            selected.push(team.id);
                            $('#id_assigned_to_groups').select2('val', selected);
                            break;
                        }
                    }
                }
            });
        });
    }

    function _setupSubmitListener() {
        // We need to check if the form has on assignee.
        var $assigned_to = angular.element('#id_assigned_to');
        var $form = $($assigned_to.closest('form')[0]);
        $form.on('submit', function(event) {

            // Check account or contact
            if ((angular.element('#id_account').val() == '' || angular.element('#id_account').val() == -1) &&
                (angular.element('#id_contact').val() == '' || angular.element('#id_contact').val() == -1)) {
                event.preventDefault();
                bootbox.dialog({
                    message: 'Please select an account or contact the case belongs to',
                    title: 'No account or contact',
                    buttons: {
                        success: {
                            label: 'Let me fix that for you',
                            className: 'btn-success'
                        }
                    }
                });
            // Check if case is assigned
            } else if ($assigned_to.val() == '' && angular.element('#id_assigned_to_groups').val() == null) {
                // No assignee, let's cancel the submit & show alert to user.
                event.preventDefault();
                bootbox.dialog({
                    message: 'Please select a colleague or team to assign the case to',
                    title: 'No assignee set',
                    buttons: {
                        success: {
                            label: 'Let me fix that for you',
                            className: 'btn-success'
                        }
                    }
                });
            }
        });
    }
}
