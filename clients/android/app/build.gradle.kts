plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

import java.util.Properties
import java.io.FileInputStream

val localProps = Properties()
rootProject.file("local.properties").let { f ->
    if (f.exists()) localProps.load(FileInputStream(f))
}

android {
    namespace = "com.pytr.tv"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.pytr.tv"
        minSdk = 21
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    signingConfigs {
        create("release") {
            storeFile = file(localProps.getProperty("release.storeFile", "../pytr-release.jks"))
            storePassword = localProps.getProperty("release.storePassword", "")
            keyAlias = localProps.getProperty("release.keyAlias", "")
            keyPassword = localProps.getProperty("release.keyPassword", "")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }

    kotlinOptions {
        jvmTarget = "1.8"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.leanback:leanback:1.0.0")
    implementation("androidx.webkit:webkit:1.9.0")
}
