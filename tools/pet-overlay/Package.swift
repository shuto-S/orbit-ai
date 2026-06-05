// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "OrbitPetOverlay",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "orbit-pet-overlay", targets: ["OrbitPetOverlay"])
    ],
    targets: [
        .executableTarget(name: "OrbitPetOverlay")
    ]
)
