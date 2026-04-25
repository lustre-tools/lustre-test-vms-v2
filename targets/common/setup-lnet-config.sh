#!/usr/bin/env bash
# Emit /etc/modprobe.d/lnet.conf body from an fc_nics= cmdline value.
#
# Consumed by the multi-NIC foundation (-oc8) and softroce backend
# (-r55).  This script is the source of truth for the emitter logic;
# ltvm_pkg/lnet_config.py mirrors it for unit testing convenience.
#
# Usage:
#     setup-lnet-config.sh                      # reads /proc/cmdline, stdout
#     setup-lnet-config.sh /path/to/lnet.conf   # writes to path
#     echo "fc_nics=tcp,softroce" | \
#         setup-lnet-config.sh --stdin          # reads cmdline from stdin
#
# fc_nics= is a comma-separated list of NIC types.  The first element
# is always the mgmt NIC type: 'tcp' (mgmt doubles as tcp0) or 'none'
# (mgmt is SSH-only, excluded from LNet -- set when the user passed
# --nic, so LNet is exactly the --nic list).  Subsequent elements
# apply to eth1, eth2, ...
#
# Net-name assignment:
#   none        -> (skipped; keeps eth slot for later entries)
#   tcp         -> tcpK(ethI)              I = eth index (slot), K =
#                                          tcp index (per-type counter)
#   softroce    -> o2ibK(ethI)             K = o2ib index, I = eth
#                                          index.  Lustre's ko2iblnd
#                                          takes a NETDEV name here and
#                                          finds the rxe ibdev via
#                                          rdma_cm -- the rxe link
#                                          itself is not a netdev and
#                                          cannot appear in lnet.conf.
#   passthrough -> o2ibK(@ib-of-ethI))     K = o2ib index, I = eth
#                                          index; '@' marker replaced
#                                          at runtime by -5a0.
set -euo pipefail

emit_lnet_conf() {
	# Args: NIC type strings, one per positional argument.  Position I
	# corresponds to ethI.  'none' skips the slot (mgmt SSH-only).
	# Writes the full lnet.conf body (one line + trailing newline) to
	# stdout.
	local -a nics=("$@")
	local -a parts=()
	local i
	local nic
	local o2ib_idx=0
	local tcp_idx=0

	for ((i=0; i < ${#nics[@]}; i++)); do
		nic=${nics[$i]}
		case "$nic" in
		none)
			# Placeholder: keep eth slot (ethI) reserved but
			# don't emit an LNet entry.  Used for mgmt (eth0)
			# when --nic is set so it's SSH-only.
			;;
		tcp)
			parts+=("tcp${tcp_idx}(eth${i})")
			tcp_idx=$((tcp_idx + 1))
			;;
		softroce)
			parts+=("o2ib${o2ib_idx}(eth${i})")
			o2ib_idx=$((o2ib_idx + 1))
			;;
		passthrough)
			parts+=("o2ib${o2ib_idx}(@ib-of-eth${i}))")
			o2ib_idx=$((o2ib_idx + 1))
			;;
		*)
			echo "setup-lnet-config: unknown NIC type '$nic'" >&2
			return 1
			;;
		esac
	done

	local joined=""
	local first=true
	local p
	for p in "${parts[@]}"; do
		if [[ $first == true ]]; then
			joined=$p
			first=false
		else
			joined="${joined},${p}"
		fi
	done

	printf 'options lnet networks="%s"\n' "$joined"
}

parse_fc_nics() {
	# Extract fc_nics= value from a kernel-cmdline-style string on
	# stdin.  Prints the comma-separated value (or empty).
	# We deliberately avoid pipefail propagation: grep returning 1
	# when fc_nics= is absent is not an error.
	tr ' ' '\n' | awk -F= '/^fc_nics=/ {sub(/^fc_nics=/, ""); \
		print; exit}'
}

main() {
	local cmdline
	local out_path=""
	local from_stdin=false

	while (( $# > 0 )); do
		case "$1" in
		--stdin)
			from_stdin=true
			shift
			;;
		-h|--help)
			sed -n '2,20p' "$0"
			return 0
			;;
		*)
			out_path=$1
			shift
			;;
		esac
	done

	if [[ $from_stdin == true ]]; then
		cmdline=$(cat)
	else
		cmdline=$(cat /proc/cmdline)
	fi

	local fc_nics
	fc_nics=$(printf '%s' "$cmdline" | parse_fc_nics)

	# fc_nics on the cmdline carries the *extras* (eth1+) only -- the
	# mgmt NIC (eth0) is implicit.  Semantics:
	#   - No --nic flag (empty fc_nics)  => mgmt doubles as tcp0
	#     (status quo).  Prepend 'tcp' so mgmt appears in LNet.
	#   - Any --nic flag (non-empty fc_nics)  => mgmt is SSH-only
	#     and must drop out of LNet.  Prepend 'none' so the eth0
	#     slot is reserved (positional eth indices preserved) but
	#     no LNet entry is emitted for it.
	local -a nic_array
	if [[ -z $fc_nics ]]; then
		nic_array=(tcp)
	else
		nic_array=(none)
		local -a _extras
		IFS=',' read -r -a _extras <<<"$fc_nics"
		nic_array+=("${_extras[@]}")
	fi

	local body
	body=$(emit_lnet_conf "${nic_array[@]}")

	if [[ -n $out_path ]]; then
		printf '%s\n' "$body" > "$out_path"
	else
		printf '%s\n' "$body"
	fi
}

# Only invoke main when run as a script (not when sourced).
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
	main "$@"
fi
