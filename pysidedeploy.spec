[app]

title = MedicalImageCheck
project_dir = .
input_file = main.py
exec_directory = build/release
project_file = pyproject.toml
icon =

[python]

python_path =
packages = Nuitka==4.1.1
android_packages =

[qt]

qml_files =
excluded_qml_plugins =
modules = Core,Gui,Widgets
plugins = platforms,imageformats

[android]

wheel_pyside =
wheel_shiboken =
plugins =

[nuitka]

macos.permissions =
mode = standalone
extra_args = --quiet --noinclude-qt-translations --windows-console-mode=disable --output-filename=MedicalImageCheck.exe --assume-yes-for-downloads

[buildozer]

mode = debug
recipe_dir =
jars_dir =
ndk_path =
sdk_path =
local_libs =
arch =
