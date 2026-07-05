@echo off
cd /d d:\training\caedecoder\radioss2inp
echo Checking ODB at %time% %date%
abaqus python check_odb.py Cell_Phone_Drop.odb > odb_check_result.txt 2>&1
echo Check exit code: %errorlevel%
echo Finished at %time% %date%
