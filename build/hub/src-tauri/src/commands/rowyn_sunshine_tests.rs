use super::*;

#[test]
fn all_documented_ports_present_is_open() {
    let listed = "47984/tcp 47989/tcp 48010/tcp 47998-48000/udp 48002/udp 48010/udp";
    assert!(missing_ports(listed).is_empty());
}

#[test]
fn the_udp_range_may_be_listed_as_single_ports() {
    let listed = "47984/tcp 47989/tcp 48010/tcp 47998/udp 47999/udp 48000/udp 48002/udp 48010/udp";
    assert!(missing_ports(listed).is_empty());
}

#[test]
fn missing_ports_are_named_exactly() {
    assert_eq!(
        missing_ports("47984/tcp 48010/udp"),
        vec![
            "47989/tcp".to_string(),
            "48010/tcp".to_string(),
            "47998-48000/udp".to_string(),
            "48002/udp".to_string(),
        ]
    );
    assert_eq!(missing_ports("").len(), 6);
}

#[test]
fn unrelated_open_ports_do_not_count() {
    let listed = "22/tcp 443/tcp 47998-48001/udp";
    assert_eq!(missing_ports(listed).len(), 6);
}
