angular.module('app.accounts').config(accountConfig);

accountConfig.$inject = ['$stateProvider'];
function accountConfig($stateProvider) {
    $stateProvider.state('base.preferences.emailaccounts.create', {
        url: '/create',
        views: {
            '@base.preferences': {
                templateUrl: 'preferences/email/controllers/emailaccount_form.html',
                controller: EmailAccountUpdateController,
                controllerAs: 'vm',
            },
        },
        ncyBreadcrumb: {
            label: 'Create',
        },
        resolve: {
            emailAccount: function() {
                return {};
            },
            user: ['User', function(User) {
                return User.me().$promise;
            }],
        },
    });

    $stateProvider.state('base.preferences.emailaccounts.setup.create', {
        url: '/:id',
        views: {
            '@': {
                templateUrl: 'preferences/email/controllers/emailaccount_form.html',
                controller: EmailAccountUpdateController,
                controllerAs: 'vm',
            },
        },
        ncyBreadcrumb: {
            label: 'Edit',
        },
        resolve: {
            emailAccount: ['EmailAccount', '$stateParams', function(EmailAccount, $stateParams) {
                return EmailAccount.get({id: $stateParams.id}).$promise;
            }],
            user: ['User', function(User) {
                return User.me().$promise;
            }],
        },
    });

    $stateProvider.state('base.preferences.emailaccounts.edit', {
        url: '/edit',
        views: {
            '@base.preferences': {
                templateUrl: 'preferences/email/controllers/emailaccount_form.html',
                controller: EmailAccountUpdateController,
                controllerAs: 'vm',
            },
        },
        ncyBreadcrumb: {
            label: 'Edit',
        },
        resolve: {
            emailAccount: ['EmailAccount', '$stateParams', function(EmailAccount, $stateParams) {
                return EmailAccount.get({id: $stateParams.id}).$promise;
            }],
            user: ['User', function(User) {
                return User.me().$promise;
            }],
        },
    });
}

angular.module('app.preferences').controller('EmailAccountCreateController', EmailAccountUpdateController);

EmailAccountUpdateController.$inject = ['$scope', '$state', '$stateParams', '$timeout', 'HLForms', 'HLSearch',
    'EmailAccount', 'emailAccount', 'user'];
function EmailAccountUpdateController($scope, $state, $stateParams, $timeout, HLForms, HLSearch,
                                            EmailAccount, emailAccount, user) {
    var vm = this;

    vm.emailAccount = emailAccount;
    vm.privacyOptions = EmailAccount.getPrivacyOptions();

    vm.saveEmailAccount = saveEmailAccount;
    vm.cancelEditing = cancelEditing;
    vm.skipEmailAccountSetup = skipEmailAccountSetup;
    vm.refreshUsers = refreshUsers;

    activate();

    ////

    function activate() {
        $timeout(function() {
            // Focus the first input on page load.
            angular.element('input')[0].focus();
            $scope.$apply();
        });
    }

    function cancelEditing() {
        $state.go('base.preferences.emailaccounts');
    }

    function skipEmailAccountSetup() {
        $state.go('base.dashboard');
    }

    function saveEmailAccount(form) {
        HLForms.blockUI();
        var cleanedAccount = HLForms.clean(angular.copy(vm.emailAccount));
        var args = {
            id: cleanedAccount.id,
            from_name: cleanedAccount.from_name,
            label: cleanedAccount.label,
            only_new: cleanedAccount.only_new,
            privacy: cleanedAccount.privacy,
            shared_with_users: cleanedAccount.shared_with_users,
        };
        console.log(args);

        HLForms.clearErrors(form);

        if (cleanedAccount.id) {
            // If there's an ID set it means we're dealing with an existing account, so update it.
            EmailAccount.patch(args).$promise.then(function() {
                toastr.success('I\'ve updated the email account for you!', 'Done');
                $state.go('base.preferences.emailaccounts');
            }, function(response) {
                _handleBadResponse(response, form);
            });
        }
    }

    function _handleBadResponse(response, form) {
        HLForms.setErrors(form, response.data);

        toastr.error('Uh oh, there seems to be a problem', 'Oops!');
    }

    function refreshUsers(query) {
        var usersPromise;

        if (!vm.users || query.length) {
            usersPromise = HLSearch.refreshList(query, 'User', 'is_active:true', 'full_name', 'full_name');

            if (usersPromise) {
                usersPromise.$promise.then(function(data) {
                    vm.users = data.objects;
                });
            }
        }
    }

    $scope.$on('saveAccount', function() {
        checkDomainForDuplicates($scope.accountForm);
    });
}
