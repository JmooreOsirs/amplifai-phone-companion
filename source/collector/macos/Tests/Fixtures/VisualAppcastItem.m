#import "VisualAppcastItem.h"
SUAppcastItem *AmplifaiVisualUpdateItem(void) {
    // Only display fields are used by this fixture, never system/version eligibility.
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
    SUAppcastItem *item = [[SUAppcastItem alloc] initWithDictionary:@{
        @"sparkle:version": @"26101426", @"sparkle:shortVersionString": @"2026.10.14-rc26",
        @"enclosure": @{@"url": @"https://example.invalid/fixture.zip", @"length": @"1"}
    }];
#pragma clang diagnostic pop
    NSCAssert(item != nil, @"Display-only appcast item must parse");
    return item;
}
