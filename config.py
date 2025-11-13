# Configuration for FileServer
PASSWORD = "Deckfileshare!%"  
ADMIN_PASSWORD = "T$umarana!1"  
MAX_USERS = 10
ALLOWED_EXTENSIONS = {
    # Documents
    "txt", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp",
    # Images
    "jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "ico", "tiff", "tif", "psd",
    # Archives
    "zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso",
    # Video
    "mp4", "mov", "avi", "mkv", "webm", "m4v", "wmv", "flv", "mpg", "mpeg", "m4v", 
    "3gp", "3g2", "f4v", "f4p", "f4a", "f4b", "ogv", "ogg", "ts", "mts", "m2ts",
    # Audio
    "mp3", "wav", "ogg", "flac", "aac", "m4a", "wma", "aiff", "aif", "ape", "opus",
    # Code/Text
    "py", "js", "html", "css", "php", "java", "c", "cpp", "h", "json", "xml", "csv",
    # Other
    "exe", "dll", "appimage", "deb", "rpm", "msi", "bat", "sh", "com",
    # Wildcard - MUST BE LAST
    "*"
}
TRUSTED_SSIDS = ["Tom & jerry", "YourTrustedSSID"]

# Folders for files
PUBLIC_FOLDER = "public"
PRIVATE_FOLDER = "private"

# Server port
SERVER_PORT = 5000
