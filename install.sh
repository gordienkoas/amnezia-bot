#!/bin/bash

SERVICE_NAME="awg_bot"
REPO_URL="https://github.com/stevefoxru/amnezia-bot.git"
REPO_API="https://api.github.com/repos/stevefoxru/amnezia-bot"
LOCAL_VERSION_FILE="/root/amnezia-bot/.version"

GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
RED=$'\033[0;31m'
BLUE=$'\033[0;34m'
NC=$'\033[0m'

ENABLE_LOGS=true
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_NAME="$(basename "$0")"
SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_NAME"

# Функция проверки обновлений на GitHub
check_github_updates() {
    local current_sha local_sha latest_sha auto_mode="$1"
    cd amnezia-bot || { echo -e "${RED}Каталог amnezia-bot не найден${NC}"; return 1; }
    
    # Получение текущего SHA коммита
    local_sha=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
    
    # Получение последнего коммита через GitHub API
    if command -v curl &>/dev/null; then
        latest_sha=$(curl -s "$REPO_API/commits/main" | jq -r '.sha' 2>/dev/null)
        [[ -z "$latest_sha" ]] && { echo -e "${RED}Не удалось получить данные с GitHub${NC}"; cd ..; return 1; }
    else
        echo -e "${RED}curl не установлен${NC}"; cd ..; return 1
    fi
    
    # Сравнение версий
    if [[ "$local_sha" == "$latest_sha" ]]; then
        echo -e "${GREEN}Репозиторий актуален (SHA: $local_sha)${NC}"
        cd ..; return 0
    fi
    
    echo -e "${YELLOW}Доступно обновление (текущий SHA: $local_sha, последний SHA: $latest_sha)${NC}"
    if [[ "$auto_mode" == "--auto" ]]; then
        # Проверка на наличие локальных изменений
        if git status --porcelain | grep -q .; then
            echo -e "${YELLOW}Обнаружены локальные изменения. Сбрасываем их...${NC}"
            run_with_spinner "Сброс локальных изменений" "git reset --hard && git clean -fd"
        fi
        run_with_spinner "Обновление репозитория" "git pull"
        if [ $? -ne 0 ]; then
            echo -e "${RED}Не удалось обновить репозиторий${NC}"
            cd ..; return 1
        fi
        echo "$latest_sha" > "$LOCAL_VERSION_FILE"
        check_script_update
        run_with_spinner "Перезапуск службы" "sudo systemctl restart $SERVICE_NAME -q"
    else
        echo -ne "${BLUE}1) Установить 2) Отменить: ${NC}"; read choice
        if [[ "$choice" == "1" ]]; then
            if git status --porcelain | grep -q .; then
                echo -e "${YELLOW}Обнаружены локальные изменения. Сбрасываем их...${NC}"
                run_with_spinner "Сброс локальных изменений" "git reset --hard && git clean -fd"
            fi
            run_with_spinner "Обновление репозитория" "git pull"
            if [ $? -ne 0 ]; then
                echo -e "${RED}Не удалось обновить репозиторий${NC}"
                cd ..; return 1
            fi
            echo "$latest_sha" > "$LOCAL_VERSION_FILE"
            check_script_update
            run_with_spinner "Перезапуск службы" "sudo systemctl restart $SERVICE_NAME -q"
        else
            echo -e "${YELLOW}Обновление отменено${NC}"
        fi
    fi
    cd ..
}

# Проверка и применение обновлений
check_updates() {
    if [[ ! -d "amnezia-bot/.git" ]]; then
        echo -e "${RED}Репозиторий не найден. Пожалуйста, установите бот сначала.${NC}"
        return 1
    fi
    check_github_updates --auto
}

# Параметры скрипта
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --quiet) ENABLE_LOGS=false ;;
        --verbose) ENABLE_LOGS=true ;;
        --check-update) check_updates; exit 0 ;;
        *)
            echo -e "${RED}Неизвестный параметр: $1${NC}"
            echo "Использование: $0 [--quiet|--verbose|--check-update]"
            exit 1
            ;;
    esac
    shift
done

# Функция запуска команд с индикатором
run_with_spinner() {
    local description="$1"; shift
    local cmd="$@"
    if [ "$ENABLE_LOGS" = true ]; then
        echo -e "${BLUE}${description}...${NC}"
        eval "$cmd"
        local stat=$?
        if [ $stat -eq 0 ]; then echo -e "${GREEN}${description}... Done!${NC}\n"; else echo -e "${RED}${description}... Failed!${NC}\n"; exit 1; fi
    else
        local out=$(mktemp) err=$(mktemp)
        eval "$cmd" >"$out" 2>"$err" & pid=$!
        local spinner='|/-\\' i=0
        while kill -0 "$pid" 2>/dev/null; do printf "\r${BLUE}${description}...${NC} ${spinner:i++%${#spinner}:1}"; sleep 0.1; done
        wait "$pid"; stat=$?
        if [ $stat -eq 0 ]; then printf "\r${BLUE}${description}...${NC} ${GREEN}Done!${NC}\n\n"; else printf "\r${BLUE}${description}...${NC} ${RED}Failed!${NC}\n\n"; echo -e "${RED}Ошибка: $cmd${NC}"; cat "$err"; rm -f "$out" "$err"; exit 1; fi
        rm -f "$out" "$err"
    fi
}

# Проверка обновления самого скрипта
check_script_update() {
    local temp_script=$(mktemp)
    if [[ -f "amnezia-bot/install.sh" ]]; then
        cp "amnezia-bot/install.sh" "$temp_script"
        if ! cmp -s "$SCRIPT_PATH" "$temp_script"; then
            echo -e "${YELLOW}Обнаружено обновление скрипта install.sh${NC}"
            run_with_spinner "Обновление скрипта" "mv $temp_script $SCRIPT_PATH && chmod +x $SCRIPT_PATH"
            echo -e "${GREEN}Скрипт обновлён, перезапускаю...${NC}"
            exec "$SCRIPT_PATH" --check-update
        else
            rm -f "$temp_script"
        fi
    else
        rm -f "$temp_script"
        echo -e "${YELLOW}Скрипт install.sh не найден в репозитории${NC}"
    fi
}

# Получение версии Ubuntu
get_ubuntu_version() {
    if command -v lsb_release &>/dev/null; then
        UBUNTU_VERSION=$(lsb_release -rs)
        UBUNTU_CODENAME=$(lsb_release -cs)
        DISTRIB_ID=$(lsb_release -is)
        [[ "$DISTRIB_ID" != "Ubuntu" ]] && { echo -e "${RED}Скрипт поддерживает только Ubuntu. Обнаружена система: $DISTRIB_ID${NC}"; exit 1; }
    else
        echo -e "${RED}lsb_release не установлен. Установите пакет lsb-release.${NC}"
        exit 1
    fi
}

# Обновление и очистка системы
update_and_clean_system() {
    while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do
        echo -e "${YELLOW}APT занят. Ждём...${NC}"; sleep 2
    done
    run_with_spinner "Обновление системы" "sudo apt-get update -qq && sudo apt-get upgrade -y -qq"
    run_with_spinner "Очистка системы" "sudo apt-get autoclean -qq && sudo apt-get autoremove --purge -y -qq"
}

# Обязательная установка Python 3.11
check_python() {
    if command -v python3.11 &>/dev/null; then
        echo -e "\n${GREEN}Python 3.11 уже установлен${NC}"; return 0
    fi
    echo -e "\n${YELLOW}Устанавливаю Python 3.11...${NC}"
    if [[ "$UBUNTU_VERSION" == "24.04" ]]; then
        local max=30 cnt=1
        while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do
            echo -e "${YELLOW}Ожидание dpkg lock ($cnt/$max)${NC}"; ((cnt++))
            [ $cnt -gt $max ] && { echo -e "${RED}Таймаут ожидания dpkg lock${NC}"; exit 1; }
            sleep 10
        done
    fi
    run_with_spinner "Установка Python 3.11" "sudo apt-get install -y software-properties-common && sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt-get update -qq && sudo apt-get install -y python3.11 python3.11-venv python3.11-dev -qq"
    command -v python3.11 &>/dev/null || { echo -e "\n${RED}Не удалось установить Python 3.11${NC}"; exit 1; }
    echo -e "\n${GREEN}Python 3.11 успешно установлен${NC}"
}

# Установка зависимостей VPN-бота
install_dependencies() {
    run_with_spinner "Установка зависимостей" "sudo apt-get install -y jq net-tools iptables resolvconf git curl -qq"
}

# Установка и конфиг needrestart
install_and_configure_needrestart() {
    run_with_spinner "Установка needrestart" "sudo apt-get install -y needrestart -qq"
    sudo sed -i 's/^#\?\(nrconf{restart} = \"\).*$/\1a\";/' /etc/needrestart/needrestart.conf
    grep -q 'nrconf{restart} = "a";' /etc/needrestart/needrestart.conf || echo 'nrconf{restart} = "a";' | sudo tee -a /etc/needrestart/needrestart.conf >/dev/null
}

# Клонирование репозитория
clone_repository() {
    if [[ -d ".git" ]]; then
        echo -e "${YELLOW}Репозиторий уже присутствует, пропускаем клонирование...${NC}"
        return
    fi
    run_with_spinner "Клонирование репозитория" "git clone $REPO_URL -q"
    cd amnezia-bot || { echo -e "${RED}Не удалось перейти в каталог amnezia-bot${NC}"; exit 1; }
}

# Настройка виртуального окружения
setup_venv() {
    if [[ -d "myenv" ]]; then return; fi
    run_with_spinner "Создание virtualenv" "python3.11 -m venv myenv"
    source myenv/bin/activate
    run_with_spinner "Установка Python-зависимостей" "pip install --upgrade pip && pip install -r requirements.txt"
    deactivate
}

# Права на скрипты
set_permissions() {
    find . -type f -name "*.sh" -exec chmod +x {} \; || exit 1
}

# Инициализация бота для генерации config
initialize_bot() {
    cd awg || exit 1
    ../myenv/bin/python3.11 bot_manager.py < /dev/tty &
    local PID=$!
    while [ ! -f "files/setting.ini" ]; do
        sleep 2
        kill -0 "$PID" 2>/dev/null || { echo -e "${RED}Ошибка инициализации бота${NC}"; exit 1; }
    done
    kill "$PID" && wait "$PID" 2>/dev/null
    cd ..
}

# Создание systemd-сервиса
create_service() {
    cat > /tmp/service_file <<EOF
[Unit]
Description=AmneziaVPN Docker Telegram Bot
After=network.target

[Service]
User=root
WorkingDirectory=/root/amnezia-bot/awg
ExecStart=/root/amnezia-bot/myenv/bin/python3.11 bot_manager.py
Restart=always

[Install]
WantedBy=multi-user.target
EOF
    run_with_spinner "Установка службы" "sudo mv /tmp/service_file /etc/systemd/system/$SERVICE_NAME.service"
    run_with_spinner "Обновление systemd" "sudo systemctl daemon-reload -q"
    run_with_spinner "Запуск службы" "sudo systemctl start $SERVICE_NAME -q"
    run_with_spinner "Включение автозапуска" "sudo systemctl enable $SERVICE_NAME -q"
}

# Удаление AmneziaWG
remove_amneziawg() {
    echo -ne "${YELLOW}Удалить AmneziaWG? (y/n): ${NC}"; read ans
    [[ "$ans" =~ ^[Yy]$ ]] || return
    systemctl is-active --quiet "$SERVICE_NAME" && run_with_spinner "Остановка" "sudo systemctl stop $SERVICE_NAME -q"
    run_with_spinner "Удаление контейнеров" "docker ps -aq -f name=amnezia-wg | xargs -r docker rm -f"
    run_with_spinner "Удаление образов" "docker images -q amneziawg/amnezia-wg | uniq | xargs -r docker rmi -f"
    run_with_spinner "Удаление конфигов" "rm -rf $(pwd)/awg/files"
}

# Меню управления службой
service_control_menu() {
    while true; do
        echo -e "\n${BLUE}Управление службой${NC}"
        sudo systemctl status "$SERVICE_NAME" | grep -E "Active:|Loaded:"
        echo -e "1) Остановить 2) Перезапустить 3) Переустановить 4) Удалить службу 5) Удалить AmneziaWG 6) Проверить обновления 7) Назад"
        echo -ne "${BLUE}Выберите: ${NC}"; read act
        case $act in
            1) run_with_spinner "Остановка" "sudo systemctl stop $SERVICE_NAME -q" ;;
            2) run_with_spinner "Перезапуск" "sudo systemctl restart $SERVICE_NAME -q" ;;
            3) reinstall_bot ;;
            4) run_with_spinner "Удаление службы" "sudo systemctl disable $SERVICE_NAME -q && sudo rm /etc/systemd/system/$SERVICE_NAME.service && sudo systemctl daemon-reload -q" ;;
            5) remove_amneziawg ;;
            6) check_updates ;;
            7) break ;;
            *) echo "Неверно" ;;
        esac
    done
}

# Переустановка бота
reinstall_bot() {
    echo -ne "${YELLOW}Переустановить бота? (y/n): ${NC}"; read ans
    [[ "$ans" =~ ^[Yy]$ ]] || return
    systemctl is-active --quiet "$SERVICE_NAME" && run_with_spinner "Остановка" "sudo systemctl stop $SERVICE_NAME -q"
    systemctl list-units --type=service --all | grep -q "$SERVICE_NAME.service" && run_with_spinner "Удаление службы" "sudo systemctl disable $SERVICE_NAME -q && sudo rm /etc/systemd/system/$SERVICE_NAME.service && sudo systemctl daemon-reload -q"
    run_with_spinner "Удаление файлов" "rm -rf amnezia-bot"
    install_bot
}

# Главное меню если установлено
installed_menu() {
    while true; do
        echo -e "\n${GREEN}1) Проверить обновления 2) Управление службой 3) Переустановить 4) Выход${NC}"
        echo -ne "${BLUE}Выберите: ${NC}"; read opt
        case $opt in
            1) check_updates ;;
            2) service_control_menu ;;
            3) reinstall_bot ;;
            4) exit 0 ;;
            *) echo "Неверно" ;;
        esac
    done
}

# Полная установка бота
install_bot() {
    get_ubuntu_version
    update_and_clean_system
    check_python
    install_dependencies
    install_and_configure_needrestart
    clone_repository
    setup_venv
    set_permissions
    initialize_bot
    create_service
    echo -e "${GREEN}Установка завершена!${NC}"
}

# Точка входа
main() {
    get_ubuntu_version
    if systemctl list-units --type=service --all | grep -q "$SERVICE_NAME.service"; then
        installed_menu
    else
        install_bot
        rm -- "$SCRIPT_PATH"
    fi
}

main
