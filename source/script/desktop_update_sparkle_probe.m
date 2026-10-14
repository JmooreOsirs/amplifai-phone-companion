// Release-only parser probe against pinned Sparkle 2.10.0. No networking/install.
// The caller must verify the exact feed signature before invoking this probe.
#import <Foundation/Foundation.h>
#import <Sparkle/Sparkle.h>

// Exact private exposed declarations verified in Sparkle's pinned 2.10.0 source.
// These are used only by this local evidence tool, never the companion runtime.
@interface SPUAppcastItemStateResolver : NSObject
- (instancetype)initWithHostVersion:(NSString *)version
       applicationVersionComparator:(id<SUVersionComparison>)applicationComparator
          standardVersionComparator:(SUStandardVersionComparator *)standardComparator;
@end

@interface SUAppcast (ReleaseProbe)
- (instancetype)initWithXMLData:(NSData *)data relativeToURL:(NSURL *)url
                 stateResolver:(SPUAppcastItemStateResolver *)resolver
       signingValidationStatus:(SPUAppcastSigningValidationStatus)status
                         error:(NSError **)error;
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 4) {
            fprintf(stderr, "usage: desktop_update_sparkle_probe verified-feed.xml old-build arm64|x86_64\n");
            return 2;
        }
        NSString *path = [NSString stringWithUTF8String:argv[1]];
        NSString *hostVersion = [NSString stringWithUTF8String:argv[2]];
        NSString *architecture = [NSString stringWithUTF8String:argv[3]];
        SUStandardVersionComparator *comparator = [SUStandardVersionComparator defaultComparator];
        SPUAppcastItemStateResolver *resolver = [[SPUAppcastItemStateResolver alloc]
            initWithHostVersion:hostVersion applicationVersionComparator:comparator standardVersionComparator:comparator];
        NSData *data = [NSData dataWithContentsOfFile:path];
        if (data == nil || data.length > 65536) { return 2; }
        NSError *error = nil;
        SUAppcast *appcast = [[SUAppcast alloc] initWithXMLData:data
            relativeToURL:[NSURL URLWithString:@"https://amplifai-database-engine.vercel.app/phone/macos/appcast.xml"]
            stateResolver:resolver signingValidationStatus:SPUAppcastSigningValidationStatusSucceeded error:&error];
        if (appcast == nil || appcast.items.count != 1) {
            fprintf(stderr, "Sparkle parser rejected feed: %s\n", error.localizedDescription.UTF8String);
            return 1;
        }
        SUAppcastItem *item = appcast.items[0];
        BOOL defaultChannel = item.channel == nil;
        BOOL greater = [comparator compareVersion:hostVersion toVersion:item.versionString] == NSOrderedAscending;
        BOOL hardware = [item.hardwareRequirements isEqualToSet:[NSSet setWithObject:architecture]];
        BOOL eligible = defaultChannel && greater && item.isMacOsUpdate && hardware
            && item.minimumOperatingSystemVersionIsOK && item.maximumOperatingSystemVersionIsOK
            && item.minimumUpdateVersionIsOK && item.arm64HardwareRequirementIsOK;
        NSDictionary *receipt = @{
            @"schema": @1, @"actualSparkleParser": @YES, @"externalSignatureVerificationRequired": @YES,
            @"hostBuild": hostVersion, @"updateBuild": item.versionString,
            @"defaultChannel": @(defaultChannel), @"greaterBuild": @(greater),
            @"exactHardware": @(hardware), @"minimumOperatingSystemVersionIsOK": @(item.minimumOperatingSystemVersionIsOK),
            @"arm64HardwareRequirementIsOK": @(item.arm64HardwareRequirementIsOK),
            @"eligibleUnderReleasedArmHostSettings": @(eligible), @"nativeInstallPerformed": @NO
        };
        NSData *json = [NSJSONSerialization dataWithJSONObject:receipt options:NSJSONWritingPrettyPrinted error:&error];
        fwrite(json.bytes, 1, json.length, stdout); fputc('\n', stdout);
        return eligible ? 0 : 1;
    }
}
