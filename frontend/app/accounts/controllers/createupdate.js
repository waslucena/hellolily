/**
 * Router definition.
 */

angular.module('app.accounts').config(accountConfig);

accountConfig.$inject = ['$stateProvider'];
function accountConfig($stateProvider) {
    $stateProvider.state('base.accounts.create', {
        url: '/create?name&phone_number',
        views: {
            '@': {
                templateUrl: 'accounts/controllers/form.html',
                controller: AccountCreateController,
                controllerAs: 'vm',
            },
        },
        ncyBreadcrumb: {
            label: 'Create',
        },
        resolve: {
            currentAccount: function() {
                return null;
            },
        },
    });

    $stateProvider.state('base.accounts.detail.edit', {
        url: '/edit',
        views: {
            '@': {
                templateUrl: 'accounts/controllers/form.html',
                controller: AccountCreateController,
                controllerAs: 'vm',
            },
        },
        ncyBreadcrumb: {
            label: 'Edit',
        },
        resolve: {
            currentAccount: ['Account', '$stateParams', function(Account, $stateParams) {
                return Account.get({id: $stateParams.id}).$promise;
            }],
        },
    });
}

/**
 * Controller to create a new account.
 */
angular.module('app.accounts').controller('AccountCreateController', AccountCreateController);

AccountCreateController.$inject = ['$scope', '$state', '$stateParams', '$timeout', 'Account', 'HLFields', 'HLForms',
    'HLUtils', 'Settings', 'User', 'currentAccount'];
function AccountCreateController($scope, $state, $stateParams, $timeout, Account, HLFields, HLForms,
                                 HLUtils, Settings, User, currentAccount) {
    var vm = this;

    vm.account = {};
    vm.accountSuggestions = [];
    vm.people = [];
    vm.tags = [];
    vm.errors = {
        name: [],
    };
    vm.useDuplicateWebsite = false;

    vm.loadDataproviderData = loadDataproviderData;
    vm.saveAccount = saveAccount;
    vm.cancelAccountCreation = cancelAccountCreation;
    vm.addRelatedField = addRelatedField;
    vm.removeRelatedField = removeRelatedField;
    vm.setStatusForCustomerId = setStatusForCustomerId;
    vm.checkExistingAccount = checkExistingAccount;

    activate();

    ////

    function activate() {
        User.query().$promise.then(function(response) {
            angular.forEach(response.results, function(user) {
                vm.people.push({
                    id: user.id,
                    // Convert to single string so searching with spaces becomes possible.
                    name: HLUtils.getFullName(user),
                });
            });
        });

        Account.getStatuses(function(response) {
            vm.statusChoices = response.results;
            vm.defaultNewStatus = Account.defaultNewStatus;
            vm.relationStatus = Account.relationStatus;
            vm.activeStatus = Account.activeStatus;

            // Getting the statusses includes which status is the default for a new account,
            // so get (or create) the account afterwards.
            _getAccount();
        });

        $timeout(function() {
            // Focus the first input on page load.
            angular.element('input')[0].focus();
            $scope.$apply();
        });
    }

    function _getAccount() {
        var company;

        // Fetch the account or create empty account
        if (currentAccount) {
            Settings.page.setAllTitles('edit', currentAccount.name);

            vm.account = currentAccount;

            angular.forEach(currentAccount.websites, function(website) {
                if (website.is_primary) {
                    vm.account.primaryWebsite = website.website;
                }
            });
            if (!vm.account.primaryWebsite || vm.account.primaryWebsite === '') {
                vm.account.primaryWebsite = '';
            }

            if (vm.account.assigned_to) {
                vm.account.assigned_to = vm.account.assigned_to.id;
            }

            if (vm.account.hasOwnProperty('social_media') && vm.account.social_media.length) {
                angular.forEach(vm.account.social_media, function(profile) {
                    vm.account[profile.name] = profile.username;
                });
            }
        } else {
            Settings.page.setAllTitles('create', 'account');

            vm.account = Account.create();
            vm.account.status = vm.defaultNewStatus;
            vm.account.name = $stateParams.name;

            if ($stateParams.phone_number !== '') {
                vm.account.phone_numbers.push({
                    'number': $stateParams.phone_number,
                    'type': 'work',
                });
            }

            User.me().$promise.then(function(user) {
                vm.account.assigned_to = user.id;
            });

            if (Settings.email.data && Settings.email.data.website) {
                vm.account.primaryWebsite = Settings.email.data.website;

                vm.account.getDataproviderInfo(Settings.email.data.website).then(function() {
                    if (!vm.account.name) {
                        company = Settings.email.data.website.split('.').slice(0, -1).join(' ');
                        vm.account.name = company.charAt(0).toUpperCase() + company.slice(1);
                    }
                });
            }
        }
    }

    function checkExistingAccount() {
        var filterQuery;
        if (!vm.account.id) {
            filterQuery = 'domain:"' + vm.account.primaryWebsite + '"' + ' OR name:"' + vm.account.name + '"';
            Account.search({filterquery: filterQuery}).$promise.then(function(results) {
                vm.accountSuggestions = results.objects;
            });
        }
    }

    function loadDataproviderData() {
        // Clear accountSuggestions because the assumption is that the user has tried with a new account.
        vm.accountSuggestions = [];

        toastr.info('Running around the world to fetch info', 'Here we go');
        vm.account.getDataproviderInfo(vm.account.primaryWebsite).then(function() {
            toastr.success('Got it!', 'Whoohoo');
        }, function() {
            toastr.error('I couldn\'t find any data', 'Sorry');
        });

        // Rerun checkExistingAccount to notify the user that he is still using an existing domain.
        vm.checkExistingAccount();
    }

    function addRelatedField(field) {
        HLFields.addRelatedField(vm.account, field);
    }

    function removeRelatedField(field, index, remove) {
        HLFields.removeRelatedField(vm.account, field, index, remove);
    }

    function cancelAccountCreation() {
        if (Settings.email.sidebar.form === 'account') {
            Settings.email.sidebar.form = null;
            Settings.email.sidebar.account = false;
        } else {
            if (Settings.page.previousState && !Settings.page.previousState.state.abstract) {
                // Check if we're coming from another page.
                $state.go(Settings.page.previousState.state, Settings.page.previousState.params);
            } else {
                $state.go('base.accounts');
            }
        }
    }

    function saveAccount(form) {
        var primaryWebsite = vm.account.primaryWebsite;
        var exists;
        var twitterId;

        // Check if an account is being added via the + account page or via a supercard.
        if (Settings.email.sidebar.isVisible) {
            ga('send', 'event', 'Account', 'Save', 'Email Sidebar');
        } else {
            ga('send', 'event', 'Account', 'Save', 'Default');
        }

        HLForms.blockUI();

        // Make sure it's not an empty website being added.
        if (primaryWebsite && primaryWebsite !== 'http://' && primaryWebsite !== 'https://') {
            exists = false;

            angular.forEach(vm.account.websites, function(website) {
                if (website.is_primary) {
                    website.website = primaryWebsite;
                    exists = true;
                }
            });

            if (!exists) {
                vm.account.websites.unshift({website: primaryWebsite, is_primary: true});
            }
        }

        // If the account is edited and the 'assigned_to' isn't changed, it's an object.
        // So if that's the case get the id and set 'assigned_to' to that value.
        if (typeof vm.account.assigned_to === 'object' && vm.account.assigned_to && vm.account.assigned_to.id) {
            vm.account.assigned_to = vm.account.assigned_to.id;
        }

        // Rewrite submit so that it isn't sending the whole status object, but only its id.
        if (typeof vm.account.status === 'object' && vm.account.status && vm.account.status.id) {
            vm.account.status = {id: vm.account.status.id};
        }

        HLForms.clearErrors(form);

        // Store the id of the current twitter object.
        if (vm.account.social_media && vm.account.social_media.length > 0) {
            twitterId = vm.account.social_media[0].id;
        }
        if (vm.account.twitter) {
            vm.account.social_media = [{
                name: 'twitter',
                username: vm.account.twitter,
            }];
            // Re-use twitter id in case of an edit.
            if (twitterId) {
                vm.account.social_media[vm.account.social_media.length - 1].id = twitterId;
            }
        }

        vm.account = HLFields.cleanRelatedFields(vm.account);

        if (vm.account.id) {
            // If there's an ID set it means we're dealing with an existing account, so update it.
            vm.account.$update(function() {
                toastr.success('I\'ve updated the account for you!', 'Done');
                $state.go('base.accounts.detail', {id: vm.account.id}, {reload: true});
            }, function(response) {
                _handleBadResponse(response, form);
            });
        } else {
            vm.account.$save(function() {
                new Intercom('trackEvent', 'account-created');

                toastr.success('I\'ve saved the account for you!', 'Yay');

                if (Settings.email.sidebar.form === 'account') {
                    if (!Settings.email.data.contact.id) {
                        Settings.email.sidebar.form = 'contact';
                        Settings.email.data.account = vm.account;
                    } else {
                        Settings.email.sidebar.account = true;
                        Settings.email.sidebar.form = null;
                    }

                    Settings.email.data.account = vm.account;
                } else {
                    $state.go('base.accounts.detail', {id: vm.account.id});
                }
            }, function(response) {
                _handleBadResponse(response, form);
            });
        }
    }

    function _handleBadResponse(response, form) {
        // Set error of the first website as the primary website error.
        if (vm.account.primaryWebsite && response.data.websites && response.data.websites.length) {
            response.data.primaryWebsite = response.data.websites.shift().website;
        }

        HLForms.setErrors(form, response.data);

        toastr.error('Uh oh, there seems to be a problem', 'Oops!');
    }

    function setStatusForCustomerId() {
        if (vm.account.status.id === vm.relationStatus.id) {
            vm.account.status = vm.activeStatus;
        }
    }

    $scope.$on('saveAccount', function() {
        saveAccount($scope.accountForm);
    });
}
