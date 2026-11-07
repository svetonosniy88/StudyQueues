#define UNICODE
#define _UNICODE
#include <windows.h>
#include <shellapi.h>
#include <shlobj.h>
#include <shlwapi.h>
#include <propkey.h>
#include <propvarutil.h>
#include <atlbase.h>
#include <string>
#include <vector>
#include <map>
#include <fstream>
#include <stdexcept>
#pragma comment(lib,"ole32.lib")
#pragma comment(lib,"shell32.lib")
#pragma comment(lib,"shlwapi.lib")
#pragma comment(lib,"propsys.lib")
#pragma comment(lib,"user32.lib")
#pragma comment(lib,"uuid.lib")

static std::wstring directory, config;
struct Profile { std::wstring kind,id,title,process,target,args,working,icon,name; };
static std::wstring Read(const wchar_t* section,const wchar_t* key) {
    wchar_t value[32768]={};GetPrivateProfileStringW(section,key,L"",value,32768,config.c_str());return value;
}
static Profile GetProfile(const std::wstring& kind) {
    auto k=kind.c_str();return {kind,Read(k,L"AppID"),Read(k,L"TitleContains"),Read(k,L"Process"),Read(k,L"Target"),Read(k,L"Arguments"),Read(k,L"Directory"),Read(k,L"Icon"),Read(k,L"DisplayName")};
}
static void Check(HRESULT hr) { if(FAILED(hr))throw std::runtime_error("Shell property operation failed"); }
static void SetText(IPropertyStore* store,REFPROPERTYKEY key,const std::wstring& text) {
    PROPVARIANT value;Check(InitPropVariantFromString(text.c_str(),&value));HRESULT hr=store->SetValue(key,value);PropVariantClear(&value);Check(hr);
}
static std::wstring Property(IPropertyStore* store,REFPROPERTYKEY key) {
    PROPVARIANT value;PropVariantInit(&value);std::wstring result;
    if(SUCCEEDED(store->GetValue(key,&value))&&value.vt==VT_LPWSTR&&value.pwszVal)result=value.pwszVal;
    PropVariantClear(&value);return result;
}
static std::wstring Launcher(const Profile& p) {return directory+L"\\"+p.kind+L".exe";}
static void Assign(IPropertyStore* store,const Profile& p,bool relaunch) {
    if(relaunch) {
        SetText(store,PKEY_AppUserModel_RelaunchCommand,L"\""+Launcher(p)+L"\"");
        SetText(store,PKEY_AppUserModel_RelaunchDisplayNameResource,p.name);
        SetText(store,PKEY_AppUserModel_RelaunchIconResource,p.icon+L",0");
    }
    PROPVARIANT allowed;InitPropVariantFromBoolean(FALSE,&allowed);Check(store->SetValue(PKEY_AppUserModel_PreventPinning,allowed));PropVariantClear(&allowed);
    SetText(store,PKEY_AppUserModel_ID,p.id);Check(store->Commit());
}
static void InstallLink(const std::wstring& path,const Profile& p) {
    if(p.id.empty()||p.target.empty())throw std::runtime_error("Incomplete launch profile");
    CComPtr<IShellLinkW> link;Check(link.CoCreateInstance(CLSID_ShellLink));
    Check(link->SetPath(Launcher(p).c_str()));Check(link->SetWorkingDirectory(directory.c_str()));Check(link->SetIconLocation(p.icon.c_str(),0));Check(link->SetDescription(p.name.c_str()));
    CComQIPtr<IPropertyStore> props(link);Assign(props,p,false);
    CComQIPtr<IPersistFile> file(link);Check(file->Save(path.c_str(),TRUE));
    SHChangeNotify(SHCNE_UPDATEITEM,SHCNF_PATHW,path.c_str(),nullptr);
}
static bool Contains(const std::wstring& value,const std::wstring& needle) {return !needle.empty()&&StrStrIW(value.c_str(),needle.c_str())!=nullptr;}
static bool Matches(HWND hwnd,const Profile& p) {
    if(GetWindow(hwnd,GW_OWNER))return false;
    wchar_t title[4096]={};GetWindowTextW(hwnd,title,4096);if(!Contains(title,p.title))return false;
    DWORD pid=0;GetWindowThreadProcessId(hwnd,&pid);
    HANDLE process=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,FALSE,pid);if(!process)return false;
    wchar_t image[32768]={};DWORD length=32768;bool match=QueryFullProcessImageNameW(process,0,image,&length)&&_wcsicmp(PathFindFileNameW(image),p.process.c_str())==0;
    CloseHandle(process);return match;
}
struct WindowSearch {const Profile* profile;std::vector<HWND> windows;};
static BOOL CALLBACK Find(HWND hwnd,LPARAM parameter) {auto* search=reinterpret_cast<WindowSearch*>(parameter);if(Matches(hwnd,*search->profile))search->windows.push_back(hwnd);return TRUE;}
static std::vector<HWND> Windows(const Profile& p) {WindowSearch search{&p,{}};EnumWindows(Find,reinterpret_cast<LPARAM>(&search));return search.windows;}
static void ApplyWindows(const Profile& p) {
    struct Icons {HICON iconSmall;HICON iconLarge;};
    static std::map<std::wstring,Icons> cache;
    if(!cache.count(p.icon))cache[p.icon]={
        static_cast<HICON>(LoadImageW(nullptr,p.icon.c_str(),IMAGE_ICON,GetSystemMetrics(SM_CXSMICON),GetSystemMetrics(SM_CYSMICON),LR_LOADFROMFILE)),
        static_cast<HICON>(LoadImageW(nullptr,p.icon.c_str(),IMAGE_ICON,GetSystemMetrics(SM_CXICON),GetSystemMetrics(SM_CYICON),LR_LOADFROMFILE))};
    for(HWND hwnd:Windows(p)) {
        CComPtr<IPropertyStore> store;
        if(FAILED(SHGetPropertyStoreForWindow(hwnd,IID_PPV_ARGS(&store))))continue;
        try {
            if(Property(store,PKEY_AppUserModel_ID)!=p.id||Property(store,PKEY_AppUserModel_RelaunchCommand)!=L"\""+Launcher(p)+L"\"")Assign(store,p,true);
            DWORD_PTR oldSmall=0,oldLarge=0;
            SendMessageTimeoutW(hwnd,WM_GETICON,ICON_SMALL,0,SMTO_ABORTIFHUNG,100,&oldSmall);
            SendMessageTimeoutW(hwnd,WM_GETICON,ICON_BIG,0,SMTO_ABORTIFHUNG,100,&oldLarge);
            auto icons=cache[p.icon];
            if(icons.iconSmall&&oldSmall!=reinterpret_cast<DWORD_PTR>(icons.iconSmall))SendMessageTimeoutW(hwnd,WM_SETICON,ICON_SMALL,reinterpret_cast<LPARAM>(icons.iconSmall),SMTO_ABORTIFHUNG,100,nullptr);
            if(icons.iconLarge&&oldLarge!=reinterpret_cast<DWORD_PTR>(icons.iconLarge))SendMessageTimeoutW(hwnd,WM_SETICON,ICON_BIG,reinterpret_cast<LPARAM>(icons.iconLarge),SMTO_ABORTIFHUNG,100,nullptr);
        } catch(...) { }
    }
}
static void StartBridge() {
    const auto path=directory+L"\\TaskbarBridge.exe";
    STARTUPINFOW si={sizeof(si)};si.dwFlags=STARTF_USESHOWWINDOW;si.wShowWindow=SW_HIDE;PROCESS_INFORMATION pi={};
    std::wstring command=L"\""+path+L"\"";
    if(CreateProcessW(path.c_str(),command.data(),nullptr,nullptr,FALSE,CREATE_NO_WINDOW,nullptr,directory.c_str(),&si,&pi)){CloseHandle(pi.hThread);CloseHandle(pi.hProcess);}
}
static void Launch(const Profile& p) {
    struct Guard {
        HANDLE handle;
        explicit Guard(const std::wstring& name):handle(CreateMutexW(nullptr,FALSE,name.c_str())) {if(!handle||WaitForSingleObject(handle,15000)!=WAIT_OBJECT_0)throw std::runtime_error("Launch already in progress");}
        ~Guard(){ReleaseMutex(handle);CloseHandle(handle);}
    } guard(L"Local\\Workstation.Launch."+p.kind);
    SetCurrentProcessExplicitAppUserModelID(p.id.c_str());StartBridge();ApplyWindows(p);
    auto windows=Windows(p);
    if(!windows.empty()){
        HWND window=windows.front();
        // ShowWindow's first call can inherit SW_HIDE from this hidden launcher.
        // Dispatch restoration to the existing window's own GUI thread instead.
        if(IsIconic(window)||!IsWindowVisible(window)){
            ShowWindowAsync(window,SW_RESTORE);
            for(int i=0;i<30&&(!IsWindowVisible(window)||IsIconic(window));i++)Sleep(10);
        }
        SetForegroundWindow(window);return;
    }
    std::wstring command=L"\""+p.target+L"\" "+p.args;
    STARTUPINFOW si={sizeof(si)};si.dwFlags=STARTF_USESHOWWINDOW;si.wShowWindow=SW_SHOWNORMAL;PROCESS_INFORMATION pi={};
    if(!CreateProcessW(p.target.c_str(),command.data(),nullptr,nullptr,FALSE,CREATE_NO_WINDOW,nullptr,p.working.c_str(),&si,&pi))throw std::runtime_error("Could not launch application");
    CloseHandle(pi.hThread);CloseHandle(pi.hProcess);
    for(int i=0;i<80;i++){Sleep(250);ApplyWindows(p);if(!Windows(p).empty())break;}
}
static std::string Utf8(const std::wstring& input) {
    int n=WideCharToMultiByte(CP_UTF8,0,input.c_str(),static_cast<int>(input.size()),nullptr,0,nullptr,nullptr);std::string output(n,'\0');
    WideCharToMultiByte(CP_UTF8,0,input.c_str(),static_cast<int>(input.size()),output.data(),n,nullptr,nullptr);return output;
}
static std::string Json(const std::wstring& text) {
    std::string input=Utf8(text),out="\"";for(char c:input){if(c=='\\'||c=='\"')out+='\\';if(c=='\n')out+="\\n";else out+=c;}return out+"\"";
}
static void Report(const std::wstring& file) {
    std::ofstream out(file,std::ios::binary);out<<"[";bool first=true;
    for(const auto& name:{L"StudyQueues",L"Journal",L"Library"}) {
        Profile p=GetProfile(name);
        for(HWND hwnd:Windows(p)) {
            CComPtr<IPropertyStore> props;if(FAILED(SHGetPropertyStoreForWindow(hwnd,IID_PPV_ARGS(&props))))continue;
            if(!first)out<<",";first=false;
            out<<"{\"kind\":"<<Json(name)<<",\"id\":"<<Json(Property(props,PKEY_AppUserModel_ID))<<",\"relaunch\":"<<Json(Property(props,PKEY_AppUserModel_RelaunchCommand))<<",\"icon\":"<<Json(Property(props,PKEY_AppUserModel_RelaunchIconResource))<<"}";
        }
    }out<<"]";
}
int WINAPI wWinMain(HINSTANCE,HINSTANCE,PWSTR,int) {
    CoInitializeEx(nullptr,COINIT_APARTMENTTHREADED);
    wchar_t module[32768]={};GetModuleFileNameW(nullptr,module,32768);wchar_t parent[32768]={};wcscpy_s(parent,module);PathRemoveFileSpecW(parent);directory=parent;config=directory+L"\\launcher.ini";
    int count=0;auto args=CommandLineToArgvW(GetCommandLineW(),&count);int result=0;
    try {
        if(count==4&&wcscmp(args[1],L"--install-link")==0)InstallLink(args[2],GetProfile(args[3]));
        else if(count==3&&wcscmp(args[1],L"--report")==0)Report(args[2]);
        else {
            std::wstring name=PathFindFileNameW(module);name=name.substr(0,name.size()-4);
            if(name==L"TaskbarBridge") {
                HANDLE mutex=CreateMutexW(nullptr,TRUE,L"Local\\ku2dm.Workstation.TaskbarBridge.v1");
                if(GetLastError()!=ERROR_ALREADY_EXISTS) {
                    std::vector<Profile> profiles;for(const auto& kind:{L"StudyQueues",L"Journal",L"Library"})profiles.push_back(GetProfile(kind));
                    for(;;){for(const auto& p:profiles)ApplyWindows(p);Sleep(1000);}
                }CloseHandle(mutex);
            } else Launch(GetProfile(name));
        }
    } catch(const std::exception&) {result=1;if(!(count>1))MessageBoxW(nullptr,L"Не удалось выполнить запуск. Проверьте launcher.ini рядом с приложением.",L"Рабочее пространство",MB_OK|MB_ICONERROR);}
    if(args)LocalFree(args);CoUninitialize();return result;
}
