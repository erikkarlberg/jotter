#!/usr/bin/env bash
set -e

PKG=jotter
VERSION=$(python3 -c "import re; print(re.search(r'VERSION\s*=\s*\"([^\"]+)\"', open('jotter/__init__.py').read()).group(1))")
BUILD_DIR=build/debian/${PKG}_${VERSION}

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}/DEBIAN"
mkdir -p "${BUILD_DIR}/usr/lib/python3/dist-packages/jotter"
mkdir -p "${BUILD_DIR}/usr/bin"
mkdir -p "${BUILD_DIR}/usr/share/applications"
mkdir -p "${BUILD_DIR}/usr/share/icons/hicolor/scalable/apps"
mkdir -p "${BUILD_DIR}/usr/share/icons/hicolor/64x64/apps"
mkdir -p "${BUILD_DIR}/usr/share/icons/hicolor/128x128/apps"
mkdir -p "${BUILD_DIR}/usr/share/icons/hicolor/256x256/apps"
mkdir -p "${BUILD_DIR}/usr/share/metainfo"

# Update version in control file
sed "s/^Version:.*/Version: ${VERSION}/" debian/control > "${BUILD_DIR}/DEBIAN/control"

# Python package
cp jotter/*.py "${BUILD_DIR}/usr/lib/python3/dist-packages/jotter/"

# Entry point wrapper
cat > "${BUILD_DIR}/usr/bin/jotter" <<'EOF'
#!/usr/bin/env python3
from jotter.main import main
main()
EOF
chmod 755 "${BUILD_DIR}/usr/bin/jotter"

# Desktop file, SVG icon, and metainfo
cp data/io.github.erikkarlberg.jotter.desktop "${BUILD_DIR}/usr/share/applications/"
cp data/icons/io.github.erikkarlberg.jotter.svg \
   "${BUILD_DIR}/usr/share/icons/hicolor/scalable/apps/"
cp data/io.github.erikkarlberg.jotter.metainfo.xml \
   "${BUILD_DIR}/usr/share/metainfo/"

# PNG icons for hicolor theme (post-install lookup + installer preview)
SVG=data/icons/io.github.erikkarlberg.jotter.svg
ICON_NAME=io.github.erikkarlberg.jotter.png
for SIZE in 64 128 256; do
    convert -background none -resize "${SIZE}x${SIZE}" "${SVG}" \
        "${BUILD_DIR}/usr/share/icons/hicolor/${SIZE}x${SIZE}/apps/${ICON_NAME}"
done

dpkg-deb --build --root-owner-group "${BUILD_DIR}" "build/${PKG}_${VERSION}.deb"
echo "Built: build/${PKG}_${VERSION}.deb"
