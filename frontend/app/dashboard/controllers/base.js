angular.module('app.dashboard').config(dashboardConfig);

dashboardConfig.$inject = ['$stateProvider'];
function dashboardConfig($stateProvider) {
    $stateProvider.state('base.dashboard', {
        url: '/',
        views: {
            '@': {
                templateUrl: 'dashboard/controllers/base.html',
                controller: DashboardController,
                controllerAs: 'db',
            },
        },
        ncyBreadcrumb: {
            label: 'Dashboard',
        },
        resolve: {
            user: ['User', function(User) {
                return User.me().$promise;
            }],
        },
    });
}

angular.module('app.dashboard').controller('DashboardController', DashboardController);

DashboardController.$inject = ['$compile', '$scope', '$state', '$templateCache', '$timeout', 'LocalStorage',
    'Settings', 'Tenant', 'user'];
function DashboardController($compile, $scope, $state, $templateCache, $timeout, LocalStorage,
                             Settings, Tenant, user) {
    var db = this;
    var storage = new LocalStorage($state.current.name + 'widgetInfo');

    if (user.info !== null && !user.info.email_account_status) {
        $state.go('base.preferences.emailaccounts.setup');
    }

    db.widgetSettings = storage.get('', {});

    db.openWidgetSettingsModal = openWidgetSettingsModal;

    Settings.page.setAllTitles('custom', 'Dashboard');

    activate();

    //////

    function activate() {
        Tenant.query({}, function(tenant) {
            db.tenant = tenant;
        });
    }

    function openWidgetSettingsModal() {
        swal({
            title: messages.alerts.dashboard.title,
            html: $compile($templateCache.get('dashboard/controllers/widget_settings.html'))($scope),
            showCancelButton: true,
            showCloseButton: true,
        }).then(function(isConfirm) {
            if (isConfirm) {
                storage.put('', db.widgetSettings);
                $state.reload();
            }
        }).done();
    }
}
