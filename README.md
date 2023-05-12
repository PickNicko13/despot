# Intro

# About

## Name

The name was chosen like such: "spotify" => "despotifier" => "despot".
It symbolizes the main author's personal despise for spotify.

## Notes

It can allocate a significant amount of RAM if you have a large collection. (~0.5GiB for my ~1.6TiB collection)

# TODO

Save length in samples

- [ ] Library issue detection
    - [ ] Different albums in one folder
    - [ ] Different albumartist in one folder
    - [ ] Different formats in one folder
    - [ ] Wrong or missing replaygain tags
    - [ ] Test files similar to `flac -t`

## Database model
```
root                str     //path to library root directory
update_time                 //last time the DB was updated
statistics: {               //precomputed statistical data
    max_peak: {
        track:      float   //maximum value of compensated peak for track_gain
        album:      float   //maximum value of compensated peak for album_gain
    }
    track_counts: {
        total:      int
        clipping:   int     //tracks that have peaks exceeding 1.0
        uploaded: {
            normal: int
            opus:   int
        }
        extension: {
            flac:   int
            mp3:    int
            ****:   int
        }
        depth: {
            16:     int
            24:     int
            **:     int
        }
        rate: {
            44100:  int
            48000:  int
            *****:  int
        }
        lacking_metadata: {
            critical:   int
            wanted:     int
        }
    }
}
last_uploaded_track: {      //path to last uploaded tracks
    normal:         str
    opus:           str
}
releases: {
    *path_N*: {
        album:      str
        tracks: {
            *filename_N*: {
                mtime:      float   //seconds since epoch to find modified files and directories
                metadata:   {}
                length:     int     //length in samples
                depth:      int     //bit depth
                rate:       int     //sampling rate
                links: {            //links to uploaded messages in telegram
                    normal: str     //without reencoding
                    opus:   str     //encoded as opus
                }
            }
        }
        files: {
            *filename_N*: {
                mtime:      float   //seconds since epoch to find modified files and directories
                links: {            //links to uploaded messages in telegram
                    normal: str     //without reencoding
                    opus:   str     //encoded as opus
                }
            }
        }
        images: {
            *filename_N*: {
                mtime:      float   //seconds since epoch to find modified files and directories
                links: {            //links to uploaded messages in telegram
                    normal: str     //without reencoding
                    opus:   str     //encoded as opus
                }
            }
        }
        links: {            //links to uploaded messages in telegram
            normal: str     //without reencoding
            opus:   str     //encoded as opus
        }
    }
}
--------
tree: {
    *name*: { //entry
        type:       str     //type in a relevant manner
        mtime:      float   //seconds since epoch to find modified files and directories
        children:   []      //if dir
        /BEGIN/ if music
        metadata:   {}
        length:     int     //length in samples
        depth:      int     //bit depth
        rate:       int     //sampling rate
        /END/ if music
        /BEGIN/ if music OR dir
        links: {            //links to uploaded messages in telegram
            normal: str     //without reencoding
            opus:   str     //encoded as opus
        }
        /END/ if music OR dir
    }
}
```

## History
### 12-05-2023
It was decided to replace recursive tree with a release-based storage
This has several benefits:
1) get rid of recursion (more predictability, no need to transfer lots of data through recursion)
2) higher performance
3) lower memory consumption
4) smaller database size
5) simpler database updates



## OFFTOP
- [ ] get the target loudness that fits at least 95% of my music, but ideally >99.9%
    - finish the library scanner functionality and use python json
        - sort tracks by loudness, find average for info, test -18 and -23 targets

### Note that there will be a Ukrainian readme-uk.md
