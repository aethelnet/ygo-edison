workspace "YGOPro"
    configurations { "Release", "Debug" }

project "ocgcore"
    kind "SharedLib"

    files { "*.cpp", "*.h" }
    
    includedirs { "/usr/include", "/usr/include/lua" }
    links { "lua", "m", "dl" }

    filter "not action:vs*"
        cppdialect "C++14"
        pic "On"

    filter "system:linux"
        defines { "LUA_USE_LINUX" }
