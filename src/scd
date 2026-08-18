# Source this file from Bash or zsh. Python resolves the saved directory, but
# cd itself must remain here so it changes the caller's interactive shell.
if [ -n "${ZSH_VERSION-}" ]; then
    eval '_scd_source=${${(%):-%x}:A}'
elif [ -n "${BASH_VERSION-}" ]; then
    _scd_source=${BASH_SOURCE[0]}
else
    printf '%s\n' 'scd: only Bash and zsh are supported' >&2
    return 2
fi

_SUTILS_SCD_ROOT=$(
    builtin cd -P -- "$(dirname -- "$_scd_source")" && pwd
) || return
unset _scd_source

scd()
{
    local scd_output scd_status scd_directory scd_marker
    scd_marker=__SCD_RESULT_V1__

    # FD 3 exists only for this assignment. It preserves ordinary sclipple
    # output while command substitution captures the directory returned on
    # FD 4. The shell restores any pre-existing FD 3 afterward.
    {
        scd_output=$(
            command python3 \
                "$_SUTILS_SCD_ROOT/sutil_modules/sutils_core.py" \
                scd --result-fd 4 -- "$@" \
                4>&1 1>&3
        )
        scd_status=$?
    } 3>&1

    [ "$scd_status" -eq 0 ] || return "$scd_status"
    [ -n "$scd_output" ] || return 0

    case $scd_output in
        "$scd_marker"$'\n'*)
            scd_directory=${scd_output#*$'\n'}
            ;;
        *)
            printf '%s\n' 'scd: invalid response from Python helper' >&2
            return 1
            ;;
    esac

    [ -n "$scd_directory" ] || return 1
    builtin cd -- "$scd_directory" || return
    printf 'cd %s\n' "$PWD"
}
