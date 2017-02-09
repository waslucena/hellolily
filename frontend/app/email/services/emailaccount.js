angular.module('app.email.services').factory('EmailAccount', EmailAccount);

EmailAccount.$inject = ['$resource'];
function EmailAccount($resource) {
    var _emailAccount = $resource(
        '/api/messaging/email/accounts/:id/',
        null,
        {
            query: {
                isArray: false,
                transformResponse: function(data) {
                    var accounts = angular.fromJson(data);

                    accounts.results.forEach(function(account) {
                        account.isPublic = (account.privacy === _emailAccount.PUBLIC);
                    });

                    return accounts;
                },
            },
            update: {
                method: 'PUT',
            },
            patch: {
                method: 'PATCH',
                params: {
                    id: '@id',
                },
            },
            shareWith: {
                method: 'POST',
                url: '/api/messaging/email/accounts/:id/shared/',
            },
            mine: {
                method: 'GET',
                url: '/api/messaging/email/accounts/mine/',
                isArray: true,
                transformResponse: function(data) {
                    var account = angular.fromJson(data);

                    account.isPublic = (account.privacy === _emailAccount.PUBLIC);

                    return account;
                },
            },
        }
    );

    _emailAccount.getPrivacyOptions = getPrivacyOptions;

    _emailAccount.PUBLIC = 0;
    _emailAccount.READONLY = 1;
    _emailAccount.METADATA = 2;
    _emailAccount.PRIVATE = 3;

    /////////

    function getPrivacyOptions() {
        // Hardcoded because these are the only privacy options.
        return [
            {id: 0, name: 'Public', text: 'sending and reading'},
            {id: 1, name: 'Read only', text: 'read only'},
            {id: 2, name: 'Metadata', text: 'title, date, recipients'},
            {id: 3, name: 'Nothing', text: 'full privacy'},
        ];
    }

    return _emailAccount;
}
