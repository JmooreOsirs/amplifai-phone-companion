#import <AppKit/AppKit.h>
#import <Sparkle/Sparkle.h>
#import <fcntl.h>
#import <unistd.h>

@interface FixtureDriver : NSObject <NSApplicationDelegate, SPUUpdaterDelegate, SPUUserDriver>
@end

@implementation FixtureDriver {
    SPUUpdater *_updater;
    NSString *_fixtureRoot;
    void (^_pendingInstall)(void);
    dispatch_source_t _releaseWatcher;
    BOOL _installReleased;
}

- (void)record:(NSDictionary *)event {
    NSData *line = [NSJSONSerialization dataWithJSONObject:event options:NSJSONWritingSortedKeys error:NULL];
    NSMutableData *encoded = [line mutableCopy];
    [encoded appendBytes:"\n" length:1];
    NSString *path = [_fixtureRoot stringByAppendingPathComponent:@"events.jsonl"];
    int fd = open(path.fileSystemRepresentation, O_WRONLY | O_APPEND | O_CREAT | O_NOFOLLOW, 0600);
    if (fd < 0 || write(fd, encoded.bytes, encoded.length) != encoded.length) { exit(10); }
    close(fd);
}

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    NSBundle *bundle = NSBundle.mainBundle;
    _fixtureRoot = [bundle objectForInfoDictionaryKey:@"AMPLIFaiFixtureRoot"];
    NSString *identifier = bundle.bundleIdentifier;
    if (![_fixtureRoot hasPrefix:@"/private/tmp/amplifai-sparkle-fixture."] ||
        ![identifier hasPrefix:@"ai.satoris.amplifai.phone.update-fixture."]) { exit(11); }
    NSString *build = [bundle objectForInfoDictionaryKey:@"CFBundleVersion"];
    if ([build isEqualToString:@"101"]) {
        NSDictionary *receipt = @{@"event": @"relaunch_verified", @"build": build, @"bundleId": identifier,
                                  @"collectorLaunched": @NO, @"ownerDataRead": @NO};
        NSData *data = [NSJSONSerialization dataWithJSONObject:receipt options:NSJSONWritingSortedKeys error:NULL];
        if (![data writeToFile:[_fixtureRoot stringByAppendingPathComponent:@"relaunch-receipt.json"] options:NSDataWritingAtomic error:NULL]) { exit(12); }
        [self record:receipt];
        [NSApp terminate:nil];
        return;
    }
    if (![build isEqualToString:@"100"]) { exit(13); }
    [self record:@{@"event": @"fixture_started", @"build": build, @"bundleId": identifier}];
    _updater = [[SPUUpdater alloc] initWithHostBundle:bundle applicationBundle:bundle userDriver:self delegate:self];
    NSError *error = nil;
    if (![_updater startUpdater:&error]) {
        [self record:@{@"event": @"fixture_failed", @"errorDomain": error.domain, @"errorCode": @(error.code)}];
        exit(14);
    }
    [_updater checkForUpdates];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 120 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{ exit(15); });
}

- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
    return _pendingInstall != nil && !_installReleased ? NSTerminateCancel : NSTerminateNow;
}

- (void)releaseIfRequested {
    if (_pendingInstall == nil || _installReleased ||
        ![NSFileManager.defaultManager fileExistsAtPath:[_fixtureRoot stringByAppendingPathComponent:@"release-install"]]) { return; }
    _installReleased = YES;
    void (^install)(void) = _pendingInstall;
    _pendingInstall = nil;
    [self record:@{@"event": @"installation_released"}];
    if (_releaseWatcher != nil) { dispatch_source_cancel(_releaseWatcher); _releaseWatcher = nil; }
    install();
}

- (BOOL)updater:(SPUUpdater *)updater shouldPostponeRelaunchForUpdate:(SUAppcastItem *)item untilInvokingBlock:(void (^)(void))handler {
    _pendingInstall = [handler copy];
    [self record:@{@"event": @"installation_deferred", @"build": item.versionString}];
    int fd = open(_fixtureRoot.fileSystemRepresentation, O_EVTONLY | O_NOFOLLOW);
    if (fd < 0) { exit(16); }
    _releaseWatcher = dispatch_source_create(DISPATCH_SOURCE_TYPE_VNODE, fd, DISPATCH_VNODE_WRITE, dispatch_get_main_queue());
    dispatch_source_set_event_handler(_releaseWatcher, ^{ [self releaseIfRequested]; });
    dispatch_source_set_cancel_handler(_releaseWatcher, ^{ close(fd); });
    dispatch_resume(_releaseWatcher);
    [self releaseIfRequested];
    return YES;
}

- (BOOL)updaterShouldRelaunchApplication:(SPUUpdater *)updater { return _installReleased; }
- (BOOL)updater:(SPUUpdater *)updater shouldDownloadReleaseNotesForUpdate:(SUAppcastItem *)item { return NO; }
- (void)updater:(SPUUpdater *)updater didFindValidUpdate:(SUAppcastItem *)item {
    [self record:@{@"event": @"signed_update_found", @"build": item.versionString}];
}
- (void)updater:(SPUUpdater *)updater didFinishUpdateCycleForUpdateCheck:(SPUUpdateCheck)check error:(NSError *)error {
    if (error != nil) {
        [self record:@{@"event": @"fixture_failed", @"errorDomain": error.domain, @"errorCode": @(error.code)}];
        exit(17);
    }
}

- (void)showUpdatePermissionRequest:(SPUUpdatePermissionRequest *)request reply:(void (^)(SUUpdatePermissionResponse *))reply {
    reply([[SUUpdatePermissionResponse alloc] initWithAutomaticUpdateChecks:NO sendSystemProfile:NO]);
}
- (void)showUserInitiatedUpdateCheckWithCancellation:(void (^)(void))cancellation {}
- (void)showUpdateFoundWithAppcastItem:(SUAppcastItem *)item state:(SPUUserUpdateState *)state reply:(void (^)(SPUUserUpdateChoice))reply { reply(SPUUserUpdateChoiceInstall); }
- (void)showUpdateReleaseNotesWithDownloadData:(SPUDownloadData *)data {}
- (void)showUpdateReleaseNotesFailedToDownloadWithError:(NSError *)error {}
- (void)showUpdateNotFoundWithError:(NSError *)error acknowledgement:(void (^)(void))acknowledgement {
    acknowledgement(); [self record:@{@"event": @"no_update"}]; exit(18);
}
- (void)showUpdaterError:(NSError *)error acknowledgement:(void (^)(void))acknowledgement {
    acknowledgement(); [self record:@{@"event": @"fixture_failed", @"errorDomain": error.domain, @"errorCode": @(error.code)}]; exit(19);
}
- (void)showDownloadInitiatedWithCancellation:(void (^)(void))cancellation {}
- (void)showDownloadDidReceiveExpectedContentLength:(uint64_t)length {}
- (void)showDownloadDidReceiveDataOfLength:(uint64_t)length {}
- (void)showDownloadDidStartExtractingUpdate {}
- (void)showExtractionReceivedProgress:(double)progress {}
- (void)showReadyToInstallAndRelaunch:(void (^)(SPUUserUpdateChoice))reply { reply(SPUUserUpdateChoiceInstall); }
- (void)showInstallingUpdateWithApplicationTerminated:(BOOL)terminated retryTerminatingApplication:(void (^)(void))retry {}
- (void)showUpdateInstalledAndRelaunched:(BOOL)relaunched acknowledgement:(void (^)(void))acknowledgement { acknowledgement(); }
- (void)dismissUpdateInstallation {}
- (void)showUpdateInFocus {}
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *application = NSApplication.sharedApplication;
        [application setActivationPolicy:NSApplicationActivationPolicyProhibited];
        FixtureDriver *driver = [FixtureDriver new];
        application.delegate = driver;
        [application run];
    }
    return 0;
}
