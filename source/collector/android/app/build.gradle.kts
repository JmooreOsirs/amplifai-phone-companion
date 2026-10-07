plugins {
    alias(libs.plugins.android.application)
}

abstract class BuildFeaturesAccess {
    @get:javax.inject.Inject
    abstract val buildFeatures: org.gradle.api.configuration.BuildFeatures
}

// Failed Gradle configuration can still be cached: reject it before reading any signing value.
require(!objects.newInstance<BuildFeaturesAccess>().buildFeatures.configurationCache.active.get()) {
    "Android signing configuration requires --no-configuration-cache before reading credentials."
}

val signingNames = listOf(
    "AMPLIFAI_ANDROID_KEYSTORE_PATH", "AMPLIFAI_ANDROID_KEY_ALIAS",
    "AMPLIFAI_ANDROID_STORE_PASSWORD", "AMPLIFAI_ANDROID_KEY_PASSWORD"
)
val signingValues = signingNames.associateWith { providers.environmentVariable(it).orNull }
val hasReleaseSigning = signingValues.values.any { !it.isNullOrBlank() }
if (hasReleaseSigning) {
    require(signingValues.values.all { !it.isNullOrBlank() }) {
        "Release signing requires all four AMPLIFAI_ANDROID signing environment variables."
    }
}

android {
    enableKotlin = false
    namespace = "ai.satoris.amplifai.phone"
    compileSdk {
        version = release(37)
    }

    defaultConfig {
        applicationId = "ai.satoris.amplifai.phone"
        minSdk = 26
        targetSdk = 37
        versionCode = 26100702
        versionName = "2026.10.07-rc2"
        testInstrumentationRunner = "ai.satoris.amplifai.phone.LocalBridgeInstrumentedTest"

    }

    signingConfigs {
        if (hasReleaseSigning) {
            create("ownerTestRelease") {
                storeFile = file(signingValues.getValue("AMPLIFAI_ANDROID_KEYSTORE_PATH")!!)
                keyAlias = signingValues.getValue("AMPLIFAI_ANDROID_KEY_ALIAS")
                storePassword = signingValues.getValue("AMPLIFAI_ANDROID_STORE_PASSWORD")
                keyPassword = signingValues.getValue("AMPLIFAI_ANDROID_KEY_PASSWORD")
            }
        }
    }
    buildTypes {
        release {
            if (hasReleaseSigning) signingConfig = signingConfigs.getByName("ownerTestRelease")
            optimization {
                enable = false
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    testImplementation(libs.junit)
}
