Unicode true

# Octop desktop NSIS installer.
# Built by `wails3 task package` on a Windows runner:
#   makensis -DARG_WAILS_AMD64_BINARY=..\..\..\bin\Octop.exe project.nsi
#   makensis -DARG_WAILS_ARM64_BINARY=..\..\..\bin\Octop.exe project.nsi

# Written by stamp_version.py nsis-defines (pep440 display + X.X.X.X file version).
!include /nonfatal "version_defines.nsh"
!include "wails_tools.nsh"

SetCompressor /SOLID lzma

# VI*Version must be numeric X.X.X.X. Display strings may be pep440 (e.g. 1.0.2b1).
!ifndef INFO_FILEVERSION
    !define INFO_FILEVERSION "${INFO_PRODUCTVERSION}.0"
!endif
VIProductVersion "${INFO_FILEVERSION}"
VIFileVersion    "${INFO_FILEVERSION}"

VIAddVersionKey "CompanyName"     "${INFO_COMPANYNAME}"
VIAddVersionKey "FileDescription" "${INFO_PRODUCTNAME} Installer"
VIAddVersionKey "ProductVersion"  "${INFO_PRODUCTVERSION}"
VIAddVersionKey "FileVersion"     "${INFO_PRODUCTVERSION}"
VIAddVersionKey "LegalCopyright"  "${INFO_COPYRIGHT}"
VIAddVersionKey "ProductName"     "${INFO_PRODUCTNAME}"

ManifestDPIAware true

!include "MUI.nsh"

!define MUI_ICON "..\icon.ico"
!define MUI_UNICON "..\icon.ico"
!define MUI_FINISHPAGE_NOAUTOCLOSE
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\${PRODUCT_EXECUTABLE}"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_RESERVEFILE_LANGDLL

LangString LANG_WAILS_WIN10_REQUIRED ${LANG_SIMPCHINESE} "本产品需要 Windows 10 1809（或 Windows Server 2019）及以上版本。"
LangString LANG_WAILS_WIN10_REQUIRED ${LANG_ENGLISH} "This product requires Windows 10 1809 (or Windows Server 2019) or later."
LangString LANG_WAILS_ARCH_NOT_SUPPORTED ${LANG_SIMPCHINESE} "当前系统架构不支持安装本产品，支持架构：${ARCH}"
LangString LANG_WAILS_ARCH_NOT_SUPPORTED ${LANG_ENGLISH} "This product can't be installed on the current Windows architecture. Supports: ${ARCH}"
LangString LANG_WAILS_INSTALL_WEBVIEW ${LANG_SIMPCHINESE} "正在安装：Microsoft Edge WebView2 运行时"
LangString LANG_WAILS_INSTALL_WEBVIEW ${LANG_ENGLISH} "Installing: Microsoft Edge WebView2 Runtime"
LangString LANG_WAILS_WEBVIEW2_MISSING ${LANG_SIMPCHINESE} "未检测到 Microsoft Edge WebView2 运行时。$\r$\n离线环境请先安装 ${WEBVIEW2_STANDALONE_INSTALLER}，或将其放在本安装程序同级目录后重试。"
LangString LANG_WAILS_WEBVIEW2_MISSING ${LANG_ENGLISH} "Microsoft Edge WebView2 Runtime was not detected.$\r$\nIn an offline environment, install ${WEBVIEW2_STANDALONE_INSTALLER} first or place it next to this installer and retry."

!define WAILS_WIN10_REQUIRED "$(LANG_WAILS_WIN10_REQUIRED)"
!define WAILS_ARCHITECTURE_NOT_SUPPORTED "$(LANG_WAILS_ARCH_NOT_SUPPORTED)"
!define WAILS_INSTALL_WEBVIEW_DETAILPRINT "$(LANG_WAILS_INSTALL_WEBVIEW)"
!define WAILS_WEBVIEW2_MISSING_ERROR "$(LANG_WAILS_WEBVIEW2_MISSING)"

Name "${INFO_PRODUCTNAME}"
!ifndef INSTALLER_OUTFILE
    !define INSTALLER_OUTFILE "..\..\..\bin\${INFO_PROJECTNAME}-desktop-windows-${ARCH}-${INFO_PRODUCTVERSION}.exe"
!endif
OutFile "${INSTALLER_OUTFILE}"
!if "${WAILS_INSTALL_SCOPE}" == "user"
    InstallDir "$LOCALAPPDATA\Programs\${INFO_PRODUCTNAME}"
!else
    InstallDir "$PROGRAMFILES64\${INFO_PRODUCTNAME}"
!endif
ShowInstDetails show

Function .onInit
    IfSilent skipLang
    !insertmacro MUI_LANGDLL_DISPLAY
    skipLang:
    !insertmacro wails.checkArchitecture
FunctionEnd

Section
    !insertmacro wails.setShellContext

    !insertmacro wails.webview2runtime

    SetOutPath $INSTDIR

    !insertmacro wails.files

    CreateShortcut "$SMPROGRAMS\${INFO_PRODUCTNAME}.lnk" "$INSTDIR\${PRODUCT_EXECUTABLE}"
    CreateShortcut "$DESKTOP\${INFO_PRODUCTNAME}.lnk" "$INSTDIR\${PRODUCT_EXECUTABLE}"

    !insertmacro wails.associateFiles
    !insertmacro wails.associateCustomProtocols

    !insertmacro wails.writeUninstaller
SectionEnd

Section "uninstall"
    !insertmacro wails.setShellContext

    RMDir /r "$AppData\${PRODUCT_EXECUTABLE}"

    RMDir /r $INSTDIR

    Delete "$SMPROGRAMS\${INFO_PRODUCTNAME}.lnk"
    Delete "$DESKTOP\${INFO_PRODUCTNAME}.lnk"

    !insertmacro wails.unassociateFiles
    !insertmacro wails.unassociateCustomProtocols

    !insertmacro wails.deleteUninstaller
SectionEnd
