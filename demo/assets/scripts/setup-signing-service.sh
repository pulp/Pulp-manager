#!/bin/sh
# Registers the signing service if it doesn't already exist

existing=$(pulpcore-manager shell -c "from pulpcore.app.models import SigningService; print(SigningService.objects.filter(name='deb_signing_service').exists())" 2>/dev/null)

if [ "$existing" != "True" ]; then
    key_id=$(GNUPGHOME=/opt/gpg gpg --list-secret-keys --with-colons 2>/dev/null | grep "^sec:" | cut -d: -f5 | head -1)
    if [ -n "$key_id" ]; then
        pulpcore-manager add-signing-service deb_signing_service /opt/scripts/deb_sign.sh "$key_id" --gnupghome /opt/gpg 2>/dev/null
        echo "Signing service 'deb_signing_service' created"
    else
        echo "No GPG key found, skipping signing service setup"
    fi
else
    echo "Signing service 'deb_signing_service' already exists"
fi
